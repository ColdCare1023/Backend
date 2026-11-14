"""BE-025: ciclo de vida de contraseñas.

Antes solo existían crear, desactivar y cambiar rol: nadie podía cambiar su
contraseña ni un administrador restablecerla, y un usuario desactivado no
podía volver a tener cuenta con su correo. Ante una contraseña comprometida
la única respuesta era desactivar la cuenta.

Toda operación que cambia la contraseña invalida las sesiones abiertas
(BE-009) y anula los enlaces y los códigos de verificación pendientes
(recuperación, activación y desafíos de login empezados con la contraseña
anterior). Los fallos de códigos de login se olvidan: quien los acumulaba ya
no conoce la contraseña. Un restablecimiento o una reactivación por un
administrador borra además todo el historial de códigos (y su tope de fallos).
"""

import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from src.application.use_cases.codigos_verificacion import (
    EstadoVerificacion,
    ServicioCodigosVerificacion,
)
from src.domain.entities.codigo_verificacion import PROPOSITOS_PASSWORD, PropositoCodigo
from src.domain.entities.usuario import Usuario
from src.domain.exceptions import (
    CodigosBloqueadosError,
    CodigoVerificacionInvalidoError,
    CredencialesInvalidasError,
    DomainError,
    RecursoNoEncontradoError,
)
from src.domain.repositories.i_password_reset_repository import IPasswordResetRepository
from src.domain.repositories.i_usuario_repository import IUsuarioRepository
from src.infrastructure.notifications.correos_cuenta import (
    correo_activacion_cuenta,
    correo_codigo_recuperacion,
    correo_recuperar_password,
    enlace_codigo,
)
from src.infrastructure.security.password_hasher import (
    hash_password_async,
    verify_password_async,
)

VIGENCIA_ENLACE_RECUPERACION = timedelta(minutes=30)
_ENLACE_INVALIDO = "El enlace de recuperación no es válido o ya caducó."
CODIGO_INVALIDO = "El código no es válido o ya caducó. Solicita uno nuevo."


def generar_password_temporal() -> str:
    """Aleatoria, de un solo uso práctico (se exige cambiarla) y conforme a la
    política de la API: ≥10 caracteres con letras y dígitos."""
    cuerpo = secrets.token_urlsafe(12)
    return cuerpo + secrets.choice(string.ascii_letters) + secrets.choice(string.digits)


def _ahora() -> datetime:
    return datetime.now(tz=timezone.utc)


async def _anular_credenciales(
    usuario_id: UUID,
    recuperacion: IPasswordResetRepository,
    codigos: ServicioCodigosVerificacion | None,
    *,
    por_administrador: bool = False,
) -> None:
    """Tras cambiar la contraseña: ni enlaces ni códigos emitidos antes siguen valiendo."""
    await recuperacion.anular_pendientes(usuario_id)
    if codigos is None:
        return
    if por_administrador:
        await codigos.reiniciar(usuario_id)
        return
    await codigos.anular_pendientes(usuario_id)
    await codigos.reiniciar(usuario_id, [PropositoCodigo.LOGIN])


class CambiarPasswordUseCase:
    """El propio usuario cambia su contraseña conociendo la actual. También
    es la forma de abandonar una contraseña temporal."""

    def __init__(
        self,
        usuarios: IUsuarioRepository,
        recuperacion: IPasswordResetRepository,
        codigos: ServicioCodigosVerificacion | None = None,
    ) -> None:
        self._usuarios = usuarios
        self._recuperacion = recuperacion
        self._codigos = codigos

    async def execute(self, usuario: Usuario, actual: str, nueva: str) -> Usuario:
        if not await verify_password_async(actual, usuario.password_hash):
            raise CredencialesInvalidasError("La contraseña actual no es correcta")
        if await verify_password_async(nueva, usuario.password_hash):
            raise DomainError("La nueva contraseña debe ser distinta de la actual")
        usuario.establecer_password(await hash_password_async(nueva), _ahora(), temporal=False, conservar_token_nuevo=True)
        await _anular_credenciales(usuario.id, self._recuperacion, self._codigos)
        return await self._usuarios.actualizar(usuario)


async def _obtener(usuarios: IUsuarioRepository, usuario_id: UUID) -> Usuario:
    usuario = await usuarios.obtener_por_id(usuario_id)
    if usuario is None:
        raise RecursoNoEncontradoError(f"Usuario {usuario_id} no encontrado")
    return usuario


class RestablecerPasswordPorAdminUseCase:
    """El administrador asigna una contraseña temporal (que se muestra una sola
    vez) y el usuario debe cambiarla en su siguiente acceso. Corta las
    sesiones abiertas: es la respuesta a una contraseña comprometida."""

    def __init__(
        self,
        usuarios: IUsuarioRepository,
        recuperacion: IPasswordResetRepository,
        codigos: ServicioCodigosVerificacion | None = None,
    ) -> None:
        self._usuarios = usuarios
        self._recuperacion = recuperacion
        self._codigos = codigos

    async def execute(self, usuario_id: UUID, actor_id: UUID) -> tuple[Usuario, str]:
        if usuario_id == actor_id:
            raise DomainError("Para su propia cuenta use el cambio de contraseña")
        usuario = await _obtener(self._usuarios, usuario_id)
        if not usuario.is_active:
            raise DomainError("El usuario está desactivado: reactívelo para asignarle una contraseña")
        temporal = generar_password_temporal()
        usuario.establecer_password(await hash_password_async(temporal), _ahora(), temporal=True)
        await _anular_credenciales(usuario.id, self._recuperacion, self._codigos, por_administrador=True)
        return await self._usuarios.actualizar(usuario), temporal


class ReactivarUsuarioUseCase:
    """Reactivación auditada de un usuario desactivado (HU-45). Recibe una
    contraseña temporal nueva: la anterior pudo ser la razón de la baja."""

    def __init__(
        self,
        usuarios: IUsuarioRepository,
        recuperacion: IPasswordResetRepository,
        codigos: ServicioCodigosVerificacion | None = None,
    ) -> None:
        self._usuarios = usuarios
        self._recuperacion = recuperacion
        self._codigos = codigos

    async def execute(self, usuario_id: UUID, actor_id: UUID) -> tuple[Usuario, str]:
        if usuario_id == actor_id:
            raise DomainError("Un administrador no puede reactivar su propia cuenta")
        usuario = await _obtener(self._usuarios, usuario_id)
        if usuario.is_active:
            raise DomainError("El usuario ya está activo")
        usuario.is_active = True
        usuario.motivo_desactivacion = None
        usuario.desactivado_en = None
        usuario.desactivado_por = None
        usuario.anonymized_for_gdpr = False
        temporal = generar_password_temporal()
        usuario.establecer_password(await hash_password_async(temporal), _ahora(), temporal=True)
        await _anular_credenciales(usuario.id, self._recuperacion, self._codigos, por_administrador=True)
        return await self._usuarios.actualizar(usuario), temporal


@dataclass(frozen=True, slots=True)
class CorreoRecuperacion:
    destino: str
    asunto: str
    cuerpo: str
    html: str | None = None


class SolicitarRecuperacionPasswordUseCase:
    """"Olvidé mi contraseña": emite un código de 6 dígitos (y, mientras el
    frontend anterior siga en uso, el enlace de un solo uso de BE-025).
    Devuelve el correo a enviar, o None si el email no corresponde a un usuario
    activo; el llamador responde igual en ambos casos para no revelar qué
    correos están registrados."""

    def __init__(
        self,
        usuarios: IUsuarioRepository,
        recuperacion: IPasswordResetRepository,
        url_restablecer: str,
        codigos: ServicioCodigosVerificacion | None = None,
        url_codigo: str = "",
        ip: str | None = None,
    ) -> None:
        self._usuarios = usuarios
        self._recuperacion = recuperacion
        self._url = url_restablecer
        self._codigos = codigos
        self._url_codigo = url_codigo
        self._ip = ip

    async def execute(self, email: str) -> CorreoRecuperacion | None:
        # Sin pasar a minúsculas: el repositorio ya compara sin distinguirlas y
        # así puede preferir la coincidencia exacta entre filas heredadas.
        usuario = await self._usuarios.obtener_por_email(email.strip())
        if usuario is None or not usuario.is_active:
            return None
        if self._codigos is not None and await self._codigos.bloqueado(usuario.id, PropositoCodigo.RECUPERACION):
            # Tope de fallos por cuenta superado: ni código ni enlace nuevos
            # hasta que pase la ventana (la respuesta al cliente es la misma).
            return None
        token = secrets.token_urlsafe(32)
        await self._recuperacion.emitir(usuario.id, token, _ahora() + VIGENCIA_ENLACE_RECUPERACION)
        minutos = int(VIGENCIA_ENLACE_RECUPERACION.total_seconds() // 60)
        # El token va en el fragmento (#): el navegador no lo envía al servidor
        # del frontend, así que no queda en sus registros de acceso.
        enlace = f"{self._url}#token={token}"
        if self._codigos is None:
            correo = correo_recuperar_password(usuario.nombre, enlace, minutos)
        else:
            try:
                emitido = await self._codigos.emitir(usuario.id, PropositoCodigo.RECUPERACION, ip=self._ip)
            except CodigosBloqueadosError:
                return None
            correo = correo_codigo_recuperacion(
                usuario.nombre,
                emitido.codigo,
                emitido.vigencia_minutos,
                enlace_codigo(self._url_codigo, usuario.email, "recuperar"),
                enlace_legado=enlace,
                minutos_enlace=minutos,
            )
        return CorreoRecuperacion(destino=usuario.email, asunto=correo.asunto, cuerpo=correo.cuerpo, html=correo.html)


class RestablecerPasswordConEnlaceUseCase:
    def __init__(
        self,
        usuarios: IUsuarioRepository,
        recuperacion: IPasswordResetRepository,
        codigos: ServicioCodigosVerificacion | None = None,
    ) -> None:
        self._usuarios = usuarios
        self._recuperacion = recuperacion
        self._codigos = codigos

    async def execute(self, token: str, nueva: str) -> Usuario:
        usuario_id = await self._recuperacion.consumir(token)
        if usuario_id is None:
            raise CredencialesInvalidasError(_ENLACE_INVALIDO)
        usuario = await self._usuarios.obtener_por_id(usuario_id)
        if usuario is None or not usuario.is_active:
            raise CredencialesInvalidasError(_ENLACE_INVALIDO)
        usuario.establecer_password(await hash_password_async(nueva), _ahora(), temporal=False)
        await _anular_credenciales(usuario.id, self._recuperacion, self._codigos)
        return await self._usuarios.actualizar(usuario)


@dataclass(frozen=True, slots=True)
class ResultadoRestablecimientoConCodigo:
    usuario: Usuario
    proposito: PropositoCodigo


class RestablecerPasswordConCodigoUseCase:
    """Fija una contraseña nueva con un código de recuperación o de activación.

    El código se compara en tiempo constante, cada fallo suma un intento en la
    base (al agotarlos el código se quema) y el consumo es atómico. Al acertar:
    contraseña propia (deja de ser temporal), cierre de todas las sesiones y
    anulación de los enlaces y códigos pendientes."""

    def __init__(
        self,
        usuarios: IUsuarioRepository,
        recuperacion: IPasswordResetRepository,
        codigos: ServicioCodigosVerificacion,
    ) -> None:
        self._usuarios = usuarios
        self._recuperacion = recuperacion
        self._codigos = codigos

    async def execute(self, email: str, codigo: str, nueva: str) -> ResultadoRestablecimientoConCodigo:
        usuario = await self._usuarios.obtener_por_email(email.strip())
        if usuario is None or not usuario.is_active:
            # Mismo trabajo que con una cuenta real (consultas y HMAC) para que
            # el tiempo de respuesta no revele si el correo está registrado.
            await self._codigos.verificar_para_usuario(uuid4(), PROPOSITOS_PASSWORD, codigo)
            raise CodigoVerificacionInvalidoError(CODIGO_INVALIDO, motivo="cuenta_no_disponible")
        resultado = await self._codigos.verificar_para_usuario(usuario.id, PROPOSITOS_PASSWORD, codigo)
        if resultado.estado != EstadoVerificacion.VALIDO or resultado.registro is None:
            raise CodigoVerificacionInvalidoError(
                CODIGO_INVALIDO, usuario_id=usuario.id, motivo=resultado.estado.value
            )
        usuario.establecer_password(await hash_password_async(nueva), _ahora(), temporal=False)
        await _anular_credenciales(usuario.id, self._recuperacion, self._codigos)
        actualizado = await self._usuarios.actualizar(usuario)
        return ResultadoRestablecimientoConCodigo(actualizado, resultado.registro.proposito)


@dataclass(frozen=True, slots=True)
class CorreoActivacion:
    destino: str
    asunto: str
    cuerpo: str
    html: str


class EmitirCodigoActivacionUseCase:
    """Cuenta creada por un administrador (o por `scripts.asegurar_admins`):
    código de activación de 48 h para que el titular elija su contraseña.
    Devuelve el correo de bienvenida; nunca contiene una contraseña."""

    def __init__(self, codigos: ServicioCodigosVerificacion, url_codigo: str, frontend_url: str = "") -> None:
        self._codigos = codigos
        self._url_codigo = url_codigo
        self._frontend_url = frontend_url

    async def execute(self, usuario: Usuario, ip: str | None = None) -> CorreoActivacion:
        # Lo decide un administrador: el tope de fallos de los códigos de
        # contraseña se reinicia para que el código nuevo se pueda usar.
        await self._codigos.reiniciar(usuario.id, PROPOSITOS_PASSWORD)
        emitido = await self._codigos.emitir(usuario.id, PropositoCodigo.ACTIVACION, ip=ip)
        horas = max(1, emitido.vigencia_segundos // 3600)
        correo = correo_activacion_cuenta(
            usuario.nombre,
            usuario.email,
            emitido.codigo,
            horas,
            enlace_codigo(self._url_codigo, usuario.email, "activar"),
            self._frontend_url,
        )
        return CorreoActivacion(destino=usuario.email, asunto=correo.asunto, cuerpo=correo.cuerpo, html=correo.html)
