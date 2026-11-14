"""Códigos de verificación de un solo uso enviados por correo.

Tres usos: segundo factor del login con contraseña, recuperación de la
contraseña y activación de cuentas que crea un administrador.

- El código son 6 dígitos de `secrets.randbelow` (CSPRNG). Con solo 10^6
  valores, la base guarda su HMAC-SHA256 con una clave que no vive en la base
  (CODIGOS_HMAC_KEY): quien lea la tabla no puede probar los 10^6 candidatos.
  El HMAC incluye usuario y propósito, así que una huella no sirve para otro.
- Se compara con `hmac.compare_digest` (tiempo constante).
- Los intentos fallidos se cuentan en la base con un UPDATE atómico; al
  llegar a CODIGO_MAX_INTENTOS el código se quema.
- Consumir es un UPDATE condicional (compare-and-set): dos peticiones con el
  mismo código no pueden usarlo ambas.
- Emitir un código nuevo anula los vivos del mismo grupo (login, o
  recuperación+activación). Los anulados no se borran: sus intentos siguen
  contando en un tope por cuenta (CODIGO_MAX_FALLOS_CUENTA en
  CODIGO_VENTANA_FALLOS_HORAS) que, superado, impide emitir y aceptar códigos
  de ese grupo. Sin él, pedir códigos nuevos daría intentos nuevos sin fin, y
  los limitadores en memoria se reinician con cada despliegue.
- Un reenvío no alarga la vida del desafío más allá de el doble de la vigencia desde la
  emisión original (`emitido_en`, inmutable).
"""

import hashlib
import hmac
import math
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from uuid import UUID

from src.domain.entities.codigo_verificacion import CodigoVerificacion, PropositoCodigo, a_utc, grupo_de
from src.domain.exceptions import CodigosBloqueadosError, ReenvioPrematuroError
from src.domain.repositories.i_codigo_verificacion_repository import ICodigoVerificacionRepository

DIGITOS = 6


def generar_codigo() -> str:
    return f"{secrets.randbelow(10**DIGITOS):0{DIGITOS}d}"


def generar_desafio() -> str:
    """Identificador opaco del login pendiente (256 bits). Viaja al navegador;
    en la base solo queda su SHA-256."""
    return secrets.token_urlsafe(32)


def huella_desafio(desafio: str) -> str:
    return hashlib.sha256(desafio.encode("utf-8")).hexdigest()


def huella_codigo(clave: bytes, usuario_id: UUID, proposito: PropositoCodigo, codigo: str) -> str:
    mensaje = f"{usuario_id}:{proposito.value}:{codigo}".encode()
    return hmac.new(clave, mensaje, hashlib.sha256).hexdigest()


def normalizar_codigo(codigo: str) -> str:
    """Admite el código copiado con espacios o guiones ("123 456", "123-456")."""
    return "".join(c for c in codigo if not c.isspace() and c != "-")


def _ahora() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass(frozen=True, slots=True)
class PoliticaCodigos:
    ttl: timedelta = timedelta(minutes=10)
    ttl_activacion: timedelta = timedelta(hours=48)
    max_intentos: int = 5
    reenvio: timedelta = timedelta(seconds=60)
    # Tope de intentos fallidos por cuenta y grupo de propósitos, sumando todos
    # los códigos emitidos dentro de la ventana.
    max_fallos_cuenta: int = 20
    ventana_fallos: timedelta = timedelta(hours=24)

    @classmethod
    def desde_settings(cls, settings) -> "PoliticaCodigos":
        return cls(
            ttl=timedelta(minutes=settings.codigo_ttl_minutos),
            ttl_activacion=timedelta(hours=settings.codigo_activacion_ttl_horas),
            max_intentos=settings.codigo_max_intentos,
            reenvio=timedelta(seconds=settings.codigo_reenvio_segundos),
            max_fallos_cuenta=settings.codigo_max_fallos_cuenta,
            ventana_fallos=timedelta(hours=settings.codigo_ventana_fallos_horas),
        )

    def vigencia(self, proposito: PropositoCodigo) -> timedelta:
        return self.ttl_activacion if proposito == PropositoCodigo.ACTIVACION else self.ttl


@dataclass(frozen=True, slots=True)
class CodigoEmitido:
    """El código en claro existe solo aquí, en memoria, hasta enviarse por correo."""

    codigo: str
    registro: CodigoVerificacion
    desafio: str | None
    vigencia: timedelta

    @property
    def vigencia_segundos(self) -> int:
        return int(self.vigencia.total_seconds())

    @property
    def vigencia_minutos(self) -> int:
        return int(self.vigencia.total_seconds() // 60)


class EstadoVerificacion(StrEnum):
    VALIDO = "valido"
    INCORRECTO = "incorrecto"
    AGOTADO = "agotado"
    CADUCADO = "caducado"
    USADO = "usado"
    INEXISTENTE = "inexistente"
    # Tope de fallos por cuenta superado: no se compara el código.
    BLOQUEADO = "bloqueado"


@dataclass(frozen=True, slots=True)
class ResultadoVerificacion:
    estado: EstadoVerificacion
    registro: CodigoVerificacion | None = None
    intentos_restantes: int = 0

    @property
    def valido(self) -> bool:
        return self.estado == EstadoVerificacion.VALIDO


class ServicioCodigosVerificacion:
    def __init__(
        self,
        repositorio: ICodigoVerificacionRepository,
        clave: bytes,
        politica: PoliticaCodigos | None = None,
        reloj: Callable[[], datetime] = _ahora,
    ) -> None:
        if not clave:
            raise ValueError("La clave HMAC de los códigos no puede estar vacía")
        self._repo = repositorio
        self._clave = clave
        self.politica = politica or PoliticaCodigos()
        self._reloj = reloj

    def _huella(self, usuario_id: UUID, proposito: PropositoCodigo, codigo: str) -> str:
        return huella_codigo(self._clave, usuario_id, proposito, codigo)

    async def bloqueado(self, usuario_id: UUID, proposito: PropositoCodigo) -> bool:
        """True si la cuenta agotó el tope de fallos del grupo de `proposito`."""
        desde = self._reloj() - self.politica.ventana_fallos
        fallos = await self._repo.contar_fallos(usuario_id, grupo_de(proposito), desde)
        return fallos >= self.politica.max_fallos_cuenta

    def _error_bloqueo(self) -> CodigosBloqueadosError:
        return CodigosBloqueadosError(int(self.politica.ventana_fallos.total_seconds()))

    async def emitir(
        self,
        usuario_id: UUID,
        proposito: PropositoCodigo,
        *,
        ip: str | None = None,
        con_desafio: bool = False,
    ) -> CodigoEmitido:
        """Lanza CodigosBloqueadosError si la cuenta superó el tope de fallos."""
        if await self.bloqueado(usuario_id, proposito):
            raise self._error_bloqueo()
        ahora = self._reloj()
        vigencia = self.politica.vigencia(proposito)
        codigo = generar_codigo()
        desafio = generar_desafio() if con_desafio else None
        registro = await self._repo.emitir(
            CodigoVerificacion(
                usuario_id=usuario_id,
                proposito=proposito,
                codigo_hash=self._huella(usuario_id, proposito, codigo),
                desafio_hash=huella_desafio(desafio) if desafio else None,
                expira_en=ahora + vigencia,
                creado_en=ahora,
                emitido_en=ahora,
                ip=ip,
            ),
            anular=grupo_de(proposito),
            purgar_antes_de=ahora - self.politica.ventana_fallos,
        )
        return CodigoEmitido(codigo=codigo, registro=registro, desafio=desafio, vigencia=vigencia)

    async def buscar_por_desafio(
        self, desafio: str, proposito: PropositoCodigo = PropositoCodigo.LOGIN
    ) -> CodigoVerificacion | None:
        if not desafio:
            return None
        return await self._repo.obtener_por_desafio(huella_desafio(desafio), proposito)

    def utilizable(self, registro: CodigoVerificacion) -> bool:
        return registro.utilizable(self._reloj(), self.politica.max_intentos)

    def segundos_para_reenviar(self, registro: CodigoVerificacion) -> int:
        transcurrido = registro.segundos_desde_emision(self._reloj())
        restante = self.politica.reenvio.total_seconds() - transcurrido
        return math.ceil(restante) if restante > 0 else 0

    async def verificar_registro(self, registro: CodigoVerificacion, codigo: str) -> ResultadoVerificacion:
        """Verifica `codigo` contra un registro concreto (login por desafío)."""
        ahora = self._reloj()
        maximo = self.politica.max_intentos
        if registro.usado_en is not None:
            estado = EstadoVerificacion.AGOTADO if registro.intentos >= maximo else EstadoVerificacion.USADO
            return ResultadoVerificacion(estado, registro)
        if registro.caducado(ahora):
            return ResultadoVerificacion(EstadoVerificacion.CADUCADO, registro)
        if registro.intentos >= maximo:
            return ResultadoVerificacion(EstadoVerificacion.AGOTADO, registro)
        if await self.bloqueado(registro.usuario_id, registro.proposito):
            return ResultadoVerificacion(EstadoVerificacion.BLOQUEADO, registro)

        esperado = self._huella(registro.usuario_id, registro.proposito, normalizar_codigo(codigo))
        if hmac.compare_digest(esperado, registro.codigo_hash):
            if await self._repo.consumir(registro.id, registro.codigo_hash, maximo, ahora):
                return ResultadoVerificacion(EstadoVerificacion.VALIDO, registro, maximo - registro.intentos)
            # Otra petición lo consumió (o lo quemó) entre la lectura y el UPDATE.
            return ResultadoVerificacion(EstadoVerificacion.USADO, registro)

        intentos = await self._repo.registrar_intento_fallido(registro.id, maximo, ahora)
        restantes = max(0, maximo - intentos) if intentos else 0
        estado = EstadoVerificacion.INCORRECTO if restantes > 0 else EstadoVerificacion.AGOTADO
        return ResultadoVerificacion(estado, registro, restantes)

    async def verificar_para_usuario(
        self, usuario_id: UUID, propositos: Iterable[PropositoCodigo], codigo: str
    ) -> ResultadoVerificacion:
        """Verifica `codigo` contra el código vigente más reciente del usuario
        para esos propósitos (recuperación o activación: el correo identifica
        la cuenta). Solo se compara contra uno: cada intento es una sola
        apuesta aunque hubiera quedado más de un código vivo.

        Hace el mismo trabajo (consultas y un HMAC) exista o no un código, para
        que el tiempo de respuesta no lo delate."""
        propositos = list(propositos)
        ahora = self._reloj()
        maximo = self.politica.max_intentos
        bloqueado = await self.bloqueado(usuario_id, propositos[0])
        vigentes = await self._repo.listar_utilizables(usuario_id, propositos, ahora, maximo)
        registro = vigentes[0] if vigentes else None
        esperado = self._huella(usuario_id, registro.proposito if registro else propositos[0], normalizar_codigo(codigo))
        if registro is None:
            return ResultadoVerificacion(EstadoVerificacion.INEXISTENTE)
        if bloqueado:
            return ResultadoVerificacion(EstadoVerificacion.BLOQUEADO, registro)
        if hmac.compare_digest(esperado, registro.codigo_hash):
            if await self._repo.consumir(registro.id, registro.codigo_hash, maximo, ahora):
                return ResultadoVerificacion(EstadoVerificacion.VALIDO, registro, maximo - registro.intentos)
            # Otra petición lo consumió (o lo quemó) entre la lectura y el UPDATE.
            return ResultadoVerificacion(EstadoVerificacion.USADO, registro)
        intentos = await self._repo.registrar_intento_fallido(registro.id, maximo, ahora)
        restantes = max(0, maximo - intentos) if intentos else 0
        estado = EstadoVerificacion.INCORRECTO if restantes > 0 else EstadoVerificacion.AGOTADO
        return ResultadoVerificacion(estado, registro, restantes)

    def limite_de_vida(self, registro: CodigoVerificacion) -> datetime:
        """Ningún reenvío lleva la caducidad más allá de el doble de la vigencia desde la emisión original."""
        origen = registro.emitido_en or registro.creado_en or self._reloj()
        return a_utc(origen) + 2 * self.politica.vigencia(registro.proposito)

    def renovable(self, registro: CodigoVerificacion) -> bool:
        """Vigente y sin llegar al límite de vida del desafío."""
        return self.utilizable(registro) and self.limite_de_vida(registro) > a_utc(self._reloj())

    async def reenviar(self, registro: CodigoVerificacion) -> CodigoEmitido:
        """Código nuevo para el mismo login pendiente (mismo desafío, mismos
        intentos acumulados). Lanza ReenvioPrematuroError durante la espera y
        CodigosBloqueadosError si la cuenta superó el tope de fallos."""
        espera = self.segundos_para_reenviar(registro)
        if espera > 0:
            raise ReenvioPrematuroError(espera)
        if await self.bloqueado(registro.usuario_id, registro.proposito):
            raise self._error_bloqueo()
        ahora = self._reloj()
        expira_en = min(ahora + self.politica.vigencia(registro.proposito), self.limite_de_vida(registro))
        codigo = generar_codigo()
        huella = self._huella(registro.usuario_id, registro.proposito, codigo)
        renovado = expira_en > ahora and await self._repo.renovar(
            registro.id, huella, expira_en, ahora, emitido_antes_de=ahora - self.politica.reenvio
        )
        if not renovado:
            # Carrera con otro reenvío, el código dejó de estar vigente o el
            # desafío llegó al límite de su vida.
            raise ReenvioPrematuroError(max(1, int(self.politica.reenvio.total_seconds())))
        registro.codigo_hash = huella
        registro.creado_en = ahora
        registro.expira_en = expira_en
        return CodigoEmitido(codigo=codigo, registro=registro, desafio=None, vigencia=expira_en - ahora)

    async def anular_pendientes(
        self, usuario_id: UUID, propositos: Iterable[PropositoCodigo] | None = None
    ) -> None:
        """Deja sin efecto los códigos vivos (cambio de contraseña, desafío
        inválido). Sus fallos siguen contando en el tope por cuenta."""
        await self._repo.anular_pendientes(
            usuario_id, self._reloj(), list(propositos) if propositos is not None else None
        )

    async def reiniciar(self, usuario_id: UUID, propositos: Iterable[PropositoCodigo] | None = None) -> None:
        """Borra el historial (y con él el tope de fallos): lo hace un
        administrador, o un cambio de contraseña para los códigos de login
        (quien los fallaba ya no conoce la contraseña)."""
        await self._repo.reiniciar(usuario_id, list(propositos) if propositos is not None else None)
