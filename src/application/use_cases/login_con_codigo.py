"""Segundo factor del login con contraseña: código de 6 dígitos por correo.

Con OTP_LOGIN_ENABLED, una contraseña correcta ya no abre sesión: crea un
login pendiente identificado por un desafío opaco (`secrets.token_urlsafe(32)`)
y envía el código al correo del titular. El token de acceso se emite solo en
`/api/auth/login/verificar`, con el desafío y el código correctos.

Un desafío pendiente muere con cualquier cambio de contraseña o invalidación
de sesiones posterior a su emisión (`tokens_invalidos_antes_de`): un login
empezado con la contraseña anterior no abre sesión después del cambio.

El login con Google no pasa por aquí: Google ya verificó la identidad del
titular (y su propio segundo factor, si lo tiene) antes de emitir el ID token.
"""

from dataclasses import dataclass
from uuid import UUID

from src.application.use_cases.codigos_verificacion import (
    CodigoEmitido,
    EstadoVerificacion,
    ResultadoVerificacion,
    ServicioCodigosVerificacion,
)
from src.domain.entities.codigo_verificacion import CodigoVerificacion, PropositoCodigo
from src.domain.entities.usuario import Usuario
from src.domain.exceptions import CodigoVerificacionInvalidoError
from src.domain.repositories.i_usuario_repository import IUsuarioRepository
from src.infrastructure.notifications.correos_cuenta import correo_codigo_login
from src.infrastructure.security.privacidad import enmascarar_email

VERIFICACION_CADUCADA = "La verificación caducó o no es válida. Vuelve a iniciar sesión."
CODIGO_INCORRECTO = "El código no es correcto."
INTENTOS_AGOTADOS = "Se agotaron los intentos para este código. Vuelve a iniciar sesión."
CUENTA_BLOQUEADA = "Se superó el número de intentos con códigos para esta cuenta. Intenta más tarde."


def _desafio_vigente(registro: CodigoVerificacion, usuario: Usuario | None) -> bool:
    """El titular sigue activo y nada invalidó sus sesiones después de emitir el desafío."""
    return usuario is not None and usuario.is_active and not registro.emitido_antes_de(usuario.tokens_invalidos_antes_de)


@dataclass(frozen=True, slots=True)
class DesafioLogin:
    usuario_id: UUID
    desafio: str
    email_enmascarado: str
    expira_en_segundos: int
    reenviar_en_segundos: int
    destino: str
    asunto: str
    cuerpo: str
    html: str


def _desafio(usuario: Usuario, desafio: str, emitido: CodigoEmitido, servicio: ServicioCodigosVerificacion) -> DesafioLogin:
    correo = correo_codigo_login(usuario.nombre, emitido.codigo, emitido.vigencia_minutos)
    return DesafioLogin(
        usuario_id=usuario.id,
        desafio=desafio,
        email_enmascarado=enmascarar_email(usuario.email),
        expira_en_segundos=emitido.vigencia_segundos,
        reenviar_en_segundos=int(servicio.politica.reenvio.total_seconds()),
        destino=usuario.email,
        asunto=correo.asunto,
        cuerpo=correo.cuerpo,
        html=correo.html,
    )


class IniciarLoginConCodigoUseCase:
    """Credenciales ya verificadas: crea el login pendiente y su código."""

    def __init__(self, codigos: ServicioCodigosVerificacion) -> None:
        self._codigos = codigos

    async def execute(self, usuario: Usuario, ip: str | None = None) -> DesafioLogin:
        emitido = await self._codigos.emitir(usuario.id, PropositoCodigo.LOGIN, ip=ip, con_desafio=True)
        assert emitido.desafio is not None
        return _desafio(usuario, emitido.desafio, emitido, self._codigos)


async def _usuario_del_desafio(
    codigos: ServicioCodigosVerificacion, usuarios: IUsuarioRepository, desafio: str
) -> tuple[CodigoVerificacion | None, Usuario | None]:
    registro = await codigos.buscar_por_desafio(desafio, PropositoCodigo.LOGIN)
    if registro is None:
        return None, None
    return registro, await usuarios.obtener_por_id(registro.usuario_id)


class ReenviarCodigoLoginUseCase:
    """Código nuevo para el mismo login pendiente (mismo desafío), respetando
    CODIGO_REENVIO_SEGUNDOS. Los intentos fallidos acumulados se conservan:
    reenviar no regala intentos."""

    def __init__(self, codigos: ServicioCodigosVerificacion, usuarios: IUsuarioRepository) -> None:
        self._codigos = codigos
        self._usuarios = usuarios

    async def localizar(self, desafio: str) -> tuple[CodigoVerificacion, Usuario]:
        registro, usuario = await _usuario_del_desafio(self._codigos, self._usuarios, desafio)
        if (
            registro is None
            or usuario is None
            or not _desafio_vigente(registro, usuario)
            or not self._codigos.renovable(registro)
        ):
            raise CodigoVerificacionInvalidoError(
                VERIFICACION_CADUCADA, usuario_id=registro.usuario_id if registro else None, motivo="desafio_invalido"
            )
        return registro, usuario

    def segundos_para_reenviar(self, registro: CodigoVerificacion) -> int:
        return self._codigos.segundos_para_reenviar(registro)

    async def execute(self, registro: CodigoVerificacion, usuario: Usuario, desafio: str) -> DesafioLogin:
        """Lanza ReenvioPrematuroError durante la espera y CodigosBloqueadosError
        si la cuenta superó el tope de fallos."""
        emitido = await self._codigos.reenviar(registro)
        return _desafio(usuario, desafio, emitido, self._codigos)


@dataclass(frozen=True, slots=True)
class ResultadoCodigoLogin:
    verificacion: ResultadoVerificacion
    usuario: Usuario | None

    @property
    def valido(self) -> bool:
        return self.verificacion.valido and self.usuario is not None and self.usuario.is_active

    @property
    def mensaje(self) -> str:
        estado = self.verificacion.estado
        if estado == EstadoVerificacion.INCORRECTO:
            return CODIGO_INCORRECTO
        if estado == EstadoVerificacion.AGOTADO:
            return INTENTOS_AGOTADOS
        if estado == EstadoVerificacion.BLOQUEADO:
            return CUENTA_BLOQUEADA
        return VERIFICACION_CADUCADA


class VerificarCodigoLoginUseCase:
    def __init__(self, codigos: ServicioCodigosVerificacion, usuarios: IUsuarioRepository) -> None:
        self._codigos = codigos
        self._usuarios = usuarios

    async def localizar(self, desafio: str) -> tuple[CodigoVerificacion | None, Usuario | None]:
        return await _usuario_del_desafio(self._codigos, self._usuarios, desafio)

    async def execute(self, registro: CodigoVerificacion, usuario: Usuario | None, codigo: str) -> ResultadoCodigoLogin:
        if not _desafio_vigente(registro, usuario):
            # Cuenta desactivada, o contraseña cambiada / sesiones cerradas
            # después de emitir el desafío: el código ya no sirve y se anula.
            await self._codigos.anular_pendientes(registro.usuario_id, [PropositoCodigo.LOGIN])
            return ResultadoCodigoLogin(ResultadoVerificacion(EstadoVerificacion.INEXISTENTE, registro), usuario)
        return ResultadoCodigoLogin(await self._codigos.verificar_registro(registro, codigo), usuario)
