from dataclasses import dataclass
from uuid import UUID

from src.domain.exceptions import CredencialesInvalidasError
from src.domain.repositories.i_usuario_repository import IUsuarioRepository
from src.infrastructure.security.jwt_handler import JWTHandler
from src.infrastructure.security.password_hasher import hash_password, verify_password

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


class AutenticarUsuarioUseCase:
    """RF-17: autenticación JWT (login) previa a la autorización RBAC."""

    def __init__(self, usuario_repository: IUsuarioRepository, jwt_handler: JWTHandler) -> None:
        self._usuario_repository = usuario_repository
        self._jwt_handler = jwt_handler

    async def execute(self, email: str, password: str) -> ResultadoAutenticacion:
        usuario = await self._usuario_repository.obtener_por_email(email)
        if usuario is None:
            verify_password(password, _HASH_SENUELO)
            raise CredencialesInvalidasError("Email o contraseña incorrectos")
        if not verify_password(password, usuario.password_hash):
            raise CredencialesInvalidasError("Email o contraseña incorrectos")
        # HU-45: un usuario desactivado no puede volver a iniciar sesión.
        if not usuario.is_active:
            raise CredencialesInvalidasError("Email o contraseña incorrectos")

        token = self._jwt_handler.crear_token(usuario.id, usuario.email, usuario.rol)
        return ResultadoAutenticacion(
            access_token=token,
            usuario_id=usuario.id,
            require_privacy_consent=usuario.requiere_aceptar_privacidad,
        )
