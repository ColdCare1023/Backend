from collections.abc import AsyncGenerator, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.usuario import Usuario
from src.domain.exceptions import CredencialesInvalidasError, PermisoDenegadoError
from src.domain.value_objects.rol import Rol
from src.infrastructure.config import Settings, get_settings
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from src.infrastructure.database.session import get_session
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


async def get_current_user(
    request: Request,
    token: Annotated[str, Depends(oauth2_scheme)],
    session: DbSessionDep,
    jwt_handler: JWTHandlerDep,
) -> Usuario:
    try:
        payload = jwt_handler.decodificar_token(token)
    except CredencialesInvalidasError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    # Revocación por jti: un token deslogueado deja de valer aunque no expire.
    if request.app.state.token_revocation.contiene(payload.jti):
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
    # HU-44: el usuario debe haber aceptado la política de privacidad.
    if not getattr(usuario, 'privacy_accepted', True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Debe aceptar la política de privacidad y consentimiento para acceder a este recurso",
        )
    return usuario


CurrentUserDep = Annotated[Usuario, Depends(get_current_user)]


async def get_current_user_sin_privacidad(
    request: Request,
    token: Annotated[str, Depends(oauth2_scheme)],
    session: DbSessionDep,
    jwt_handler: JWTHandlerDep,
) -> Usuario:
    try:
        payload = jwt_handler.decodificar_token(token)
    except CredencialesInvalidasError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if request.app.state.token_revocation.contiene(payload.jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El token fue revocado",
            headers={"WWW-Authenticate": "Bearer"},
        )

    repositorio = SQLAlchemyUsuarioRepository(session)
    usuario = await repositorio.obtener_por_id(payload.sub)
    if usuario is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no encontrado")
    # HU-45: usuario desactivado no puede acceder.
    if not usuario.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario desactivado")
    return usuario


CurrentUserSinPrivacidadDep = Annotated[Usuario, Depends(get_current_user_sin_privacidad)]


def require_roles(*roles_permitidos: Rol) -> Callable[[Usuario], Usuario]:
    def dependencia(usuario: CurrentUserDep) -> Usuario:
        try:
            verificar_permiso(usuario.rol, roles_permitidos)
        except PermisoDenegadoError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        return usuario

    return dependencia
