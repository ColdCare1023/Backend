from collections.abc import AsyncGenerator, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.usuario import Usuario
from src.domain.exceptions import CredencialesInvalidasError, PermisoDenegadoError
from src.domain.value_objects.rol import Rol
from src.infrastructure.config import Settings, get_settings
from src.infrastructure.database.repositories.jti_repository import SQLAlchemyJtiRepository
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from src.infrastructure.database.session import get_session
from src.infrastructure.notifications.correo_transaccional import EnviadorCorreo
from src.infrastructure.security.jwt_handler import JWTHandler
from src.infrastructure.security.rbac import verificar_permiso

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_session():
        yield session


DbSessionDep = Annotated[AsyncSession, Depends(get_db)]


def get_jwt_handler(settings: SettingsDep) -> JWTHandler:
    return JWTHandler(settings)


JWTHandlerDep = Annotated[JWTHandler, Depends(get_jwt_handler)]


async def _usuario_de_token_vigente(token: str, session: AsyncSession, jwt_handler: JWTHandler) -> Usuario:
    """Decodifica el token y comprueba que la sesión siga vigente.

    BE-009: la revocación vive en la base de datos (`jti_invalidados` y
    `users.tokens_invalidos_antes_de`): sobrevive a reinicios y vale igual en
    todas las instancias."""
    try:
        payload = jwt_handler.decodificar_token(token)
    except CredencialesInvalidasError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    # Revocación por jti: un token deslogueado deja de valer aunque no expire.
    if await SQLAlchemyJtiRepository(session).esta_invalidado(payload.jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El token fue revocado",
            headers={"WWW-Authenticate": "Bearer"},
        )

    repositorio = SQLAlchemyUsuarioRepository(session)
    usuario = await repositorio.obtener_por_id(payload.sub)
    if usuario is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no encontrado")
    # HU-45: un token emitido antes de la desactivación deja de ser válido.
    if not usuario.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario desactivado")
    if not usuario.token_vigente(payload.iat):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La sesión fue invalidada",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return usuario


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: DbSessionDep,
    jwt_handler: JWTHandlerDep,
) -> Usuario:
    usuario = await _usuario_de_token_vigente(token, session, jwt_handler)
    # HU-44 / BE-014: el usuario debe haber aceptado la versión VIGENTE de la
    # política de privacidad; no basta un indicador que nunca se pidió.
    if usuario.requiere_aceptar_privacidad:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Debe aceptar la política de privacidad y consentimiento para acceder a este recurso",
        )
    # BE-025: una contraseña temporal (asignada por un administrador) solo
    # sirve para elegir una propia en POST /api/auth/cambiar-password.
    if usuario.debe_cambiar_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Debe cambiar la contraseña temporal antes de continuar",
        )
    return usuario


CurrentUserDep = Annotated[Usuario, Depends(get_current_user)]


async def get_current_user_sin_privacidad(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: DbSessionDep,
    jwt_handler: JWTHandlerDep,
) -> Usuario:
    return await _usuario_de_token_vigente(token, session, jwt_handler)


CurrentUserSinPrivacidadDep = Annotated[Usuario, Depends(get_current_user_sin_privacidad)]


def get_enviador_correo(settings: SettingsDep) -> EnviadorCorreo:
    """BE-025: sustituible en pruebas (no se sale a la red)."""
    return EnviadorCorreo(settings)


EnviadorCorreoDep = Annotated[EnviadorCorreo, Depends(get_enviador_correo)]


def require_roles(*roles_permitidos: Rol) -> Callable[[Usuario], Usuario]:
    def dependencia(usuario: CurrentUserDep) -> Usuario:
        try:
            verificar_permiso(usuario.rol, roles_permitidos)
        except PermisoDenegadoError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        return usuario

    return dependencia
