"""BE-025: ciclo de vida de contraseñas.

Antes solo existían crear, desactivar y cambiar rol: nadie podía cambiar su
contraseña ni un administrador restablecerla, y un usuario desactivado no
podía volver a tener cuenta con su correo. Ante una contraseña comprometida
la única respuesta era desactivar la cuenta.

Toda operación que cambia la contraseña invalida las sesiones abiertas
(BE-009) y anula los enlaces de recuperación pendientes.
"""

import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from src.domain.entities.usuario import Usuario
from src.domain.exceptions import CredencialesInvalidasError, DomainError, RecursoNoEncontradoError
from src.domain.repositories.i_password_reset_repository import IPasswordResetRepository
from src.domain.repositories.i_usuario_repository import IUsuarioRepository
from src.infrastructure.security.password_hasher import (
    hash_password_async,
    verify_password_async,
)

VIGENCIA_ENLACE_RECUPERACION = timedelta(minutes=30)
_ENLACE_INVALIDO = "El enlace de recuperación no es válido o ya caducó."


def generar_password_temporal() -> str:
    """Aleatoria, de un solo uso práctico (se exige cambiarla) y conforme a la
    política de la API: ≥10 caracteres con letras y dígitos."""
    cuerpo = secrets.token_urlsafe(12)
    return cuerpo + secrets.choice(string.ascii_letters) + secrets.choice(string.digits)


def _ahora() -> datetime:
    return datetime.now(tz=timezone.utc)


class CambiarPasswordUseCase:
    """El propio usuario cambia su contraseña conociendo la actual. También
    es la forma de abandonar una contraseña temporal."""

    def __init__(self, usuarios: IUsuarioRepository, recuperacion: IPasswordResetRepository) -> None:
        self._usuarios = usuarios
        self._recuperacion = recuperacion

    async def execute(self, usuario: Usuario, actual: str, nueva: str) -> Usuario:
        if not await verify_password_async(actual, usuario.password_hash):
            raise CredencialesInvalidasError("La contraseña actual no es correcta")
        if await verify_password_async(nueva, usuario.password_hash):
            raise DomainError("La nueva contraseña debe ser distinta de la actual")
        usuario.establecer_password(await hash_password_async(nueva), _ahora(), temporal=False, conservar_token_nuevo=True)
        await self._recuperacion.anular_pendientes(usuario.id)
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

    def __init__(self, usuarios: IUsuarioRepository, recuperacion: IPasswordResetRepository) -> None:
        self._usuarios = usuarios
        self._recuperacion = recuperacion

    async def execute(self, usuario_id: UUID, actor_id: UUID) -> tuple[Usuario, str]:
        if usuario_id == actor_id:
            raise DomainError("Para su propia cuenta use el cambio de contraseña")
        usuario = await _obtener(self._usuarios, usuario_id)
        if not usuario.is_active:
            raise DomainError("El usuario está desactivado: reactívelo para asignarle una contraseña")
        temporal = generar_password_temporal()
        usuario.establecer_password(await hash_password_async(temporal), _ahora(), temporal=True)
        await self._recuperacion.anular_pendientes(usuario.id)
        return await self._usuarios.actualizar(usuario), temporal


class ReactivarUsuarioUseCase:
    """Reactivación auditada de un usuario desactivado (HU-45). Recibe una
    contraseña temporal nueva: la anterior pudo ser la razón de la baja."""

    def __init__(self, usuarios: IUsuarioRepository, recuperacion: IPasswordResetRepository) -> None:
        self._usuarios = usuarios
        self._recuperacion = recuperacion

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
        await self._recuperacion.anular_pendientes(usuario.id)
        return await self._usuarios.actualizar(usuario), temporal


@dataclass(frozen=True, slots=True)
class CorreoRecuperacion:
    destino: str
    asunto: str
    cuerpo: str


class SolicitarRecuperacionPasswordUseCase:
    """"Olvidé mi contraseña": emite un enlace aleatorio de un solo uso y vida
    corta. Devuelve el correo a enviar, o None si el email no corresponde a
    un usuario activo; el llamador responde igual en ambos casos para no
    revelar qué correos están registrados."""

    def __init__(
        self, usuarios: IUsuarioRepository, recuperacion: IPasswordResetRepository, url_restablecer: str
    ) -> None:
        self._usuarios = usuarios
        self._recuperacion = recuperacion
        self._url = url_restablecer

    async def execute(self, email: str) -> CorreoRecuperacion | None:
        usuario = await self._usuarios.obtener_por_email(email)
        if usuario is None or not usuario.is_active:
            return None
        token = secrets.token_urlsafe(32)
        await self._recuperacion.emitir(usuario.id, token, _ahora() + VIGENCIA_ENLACE_RECUPERACION)
        minutos = int(VIGENCIA_ENLACE_RECUPERACION.total_seconds() // 60)
        # El token va en el fragmento (#): el navegador no lo envía al servidor
        # del frontend, así que no queda en sus registros de acceso.
        enlace = f"{self._url}#token={token}"
        return CorreoRecuperacion(
            destino=usuario.email,
            asunto="[ThermoTrace] Restablecer contraseña",
            cuerpo=(
                f"Hola {usuario.nombre}:\n\n"
                "Recibimos una solicitud para restablecer la contraseña de tu cuenta.\n"
                f"Usa este enlace en los próximos {minutos} minutos (solo funciona una vez):\n\n"
                f"{enlace}\n\n"
                "Si no lo solicitaste, ignora este mensaje: tu contraseña no cambia."
            ),
        )


class RestablecerPasswordConEnlaceUseCase:
    def __init__(self, usuarios: IUsuarioRepository, recuperacion: IPasswordResetRepository) -> None:
        self._usuarios = usuarios
        self._recuperacion = recuperacion

    async def execute(self, token: str, nueva: str) -> Usuario:
        usuario_id = await self._recuperacion.consumir(token)
        if usuario_id is None:
            raise CredencialesInvalidasError(_ENLACE_INVALIDO)
        usuario = await self._usuarios.obtener_por_id(usuario_id)
        if usuario is None or not usuario.is_active:
            raise CredencialesInvalidasError(_ENLACE_INVALIDO)
        usuario.establecer_password(await hash_password_async(nueva), _ahora(), temporal=False)
        await self._recuperacion.anular_pendientes(usuario.id)
        return await self._usuarios.actualizar(usuario)
