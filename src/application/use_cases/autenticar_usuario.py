from dataclasses import dataclass
from uuid import UUID

from src.domain.entities.usuario import Usuario
from src.domain.exceptions import CredencialesInvalidasError
from src.domain.repositories.i_usuario_repository import IUsuarioRepository
from src.infrastructure.security.jwt_handler import JWTHandler
from src.infrastructure.security.password_hasher import (
    hash_password,
    hash_password_async,
    necesita_rehash,
    verify_password_async,
)

# Hash señuelo: cuando el email no existe se verifica igualmente contra este
# hash para que la respuesta tarde lo mismo que con un usuario real. Sin esto,
# la diferencia de tiempo permitiría enumerar qué emails están registrados.
_HASH_SENUELO = hash_password("senuelo-para-igualar-tiempos")


@dataclass(frozen=True, slots=True)
class ResultadoAutenticacion:
    access_token: str
    usuario_id: UUID
    token_type: str = "bearer"
    # HU-44: el frontend debe bloquear la navegación hasta que se acepte.
    require_privacy_consent: bool = False
    # BE-025: contraseña temporal pendiente de cambio.
    require_password_change: bool = False


class AutenticarUsuarioUseCase:
    """RF-17: autenticación JWT (login) previa a la autorización RBAC."""

    def __init__(self, usuario_repository: IUsuarioRepository, jwt_handler: JWTHandler) -> None:
        self._usuario_repository = usuario_repository
        self._jwt_handler = jwt_handler

    async def execute(self, email: str, password: str) -> ResultadoAutenticacion:
        return self.emitir_sesion(await self.verificar_credenciales(email, password))

    async def verificar_credenciales(self, email: str, password: str) -> Usuario:
        """Comprueba correo y contraseña sin emitir sesión. Con el segundo
        factor activo (OTP_LOGIN_ENABLED) el token se emite solo tras el código."""
        # El repositorio compara sin distinguir mayúsculas y, entre filas
        # heredadas que solo difieren en ellas, prefiere la que coincide
        # exactamente: por eso el correo no se pasa a minúsculas aquí.
        # HU-50: el mensaje es el mismo en los tres casos (no revela si la
        # cuenta existe); la excepción lleva el usuario y el motivo para que
        # LOGIN_FALLIDO se pueda correlacionar por actor en la bitácora.
        usuario = await self._usuario_repository.obtener_por_email(email.strip())
        if usuario is None:
            await verify_password_async(password, _HASH_SENUELO)
            raise CredencialesInvalidasError("Email o contraseña incorrectos", motivo="cuenta_inexistente")
        if not await verify_password_async(password, usuario.password_hash):
            raise CredencialesInvalidasError(
                "Email o contraseña incorrectos", usuario_id=usuario.id, motivo="password_incorrecta"
            )
        # HU-45: un usuario desactivado no puede volver a iniciar sesión.
        if not usuario.is_active:
            raise CredencialesInvalidasError(
                "Email o contraseña incorrectos", usuario_id=usuario.id, motivo="usuario_desactivado"
            )
        # BE-021: los hashes heredados (bcrypt) o con parámetros antiguos se
        # sustituyen por Argon2id vigente; el llamador confirma la transacción.
        if necesita_rehash(usuario.password_hash):
            usuario.password_hash = await hash_password_async(password)
            usuario = await self._usuario_repository.actualizar(usuario)
        return usuario

    def emitir_sesion(self, usuario: Usuario) -> ResultadoAutenticacion:
        token = self._jwt_handler.crear_token(usuario.id, usuario.email, usuario.rol)
        return ResultadoAutenticacion(
            access_token=token,
            usuario_id=usuario.id,
            require_privacy_consent=usuario.requiere_aceptar_privacidad,
            require_password_change=usuario.debe_cambiar_password,
        )
