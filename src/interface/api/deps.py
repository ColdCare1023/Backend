import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.codigos_verificacion import PoliticaCodigos, ServicioCodigosVerificacion
from src.domain.entities.usuario import Usuario
from src.domain.exceptions import CredencialesInvalidasError, PermisoDenegadoError
from src.domain.value_objects.resultado_auditoria import ResultadoAuditoria
from src.domain.value_objects.rol import Rol
from src.infrastructure.config import Settings, get_settings
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.infrastructure.database.repositories.codigo_verificacion_repository import (
    SQLAlchemyCodigoVerificacionRepository,
)
from src.infrastructure.database.repositories.jti_repository import SQLAlchemyJtiRepository
from src.infrastructure.database.repositories.trazabilidad_repository import SQLAlchemyTrazabilidadRepository
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from src.infrastructure.database.session import get_session
from src.infrastructure.notifications.correo_transaccional import EnviadorCorreo
from src.infrastructure.security.jwt_handler import JWTHandler
from src.infrastructure.security.rbac import verificar_permiso
from src.interface.limitador_auditoria import LIMITE_AUDITORIA

logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_session():
        yield session


DbSessionDep = Annotated[AsyncSession, Depends(get_db)]


def get_jwt_handler(settings: SettingsDep) -> JWTHandler:
    return JWTHandler(settings)


JWTHandlerDep = Annotated[JWTHandler, Depends(get_jwt_handler)]


def _ruta_de(request: Request | None) -> str:
    """Plantilla de la ruta (`/api/usuarios/{usuario_id}`) en vez de la URL
    concreta: agrupa los intentos y no vuelca identificadores a la bitácora."""
    if request is None:
        return "desconocida"
    ruta = getattr(request.scope.get("route"), "path", None)
    return ruta or request.url.path


async def auditar_rechazo_acceso(
    session: AsyncSession,
    request: Request | None,
    *,
    accion: str,
    usuario_id: UUID | None,
    detalle: dict,
    clave: str,
) -> None:
    """HU-50 esc. 1 y 3: deja en la bitácora un rechazo de acceso (RBAC o
    sesión) con actor, IP, ruta y resultado `denegado`.

    Estas rutas las puede disparar cualquiera con un token válido o recién
    invalidado, y audit_logs no admite borrado: se audita como mucho una fila
    por clave (actor, motivo o ruta) y ventana, con los intentos que solo se
    contaron desde la anterior. Un fallo al auditar nunca convierte el 401/403
    en un 500."""
    sin_auditar = LIMITE_AUDITORIA.permitir(clave)
    if sin_auditar is None:
        return
    metodo = request.method if request is not None else "?"
    ruta = _ruta_de(request)
    try:
        await AuditarAccionCriticaUseCase(
            SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
        ).execute(
            usuario_id=usuario_id,
            accion=accion,
            recurso=f"{metodo} {ruta}"[:100],
            detalle={**detalle, "metodo": metodo, "ruta": ruta, "intentos_sin_auditar": sin_auditar},
            ip_origen=request.client.host if request is not None and request.client else None,
            resultado=ResultadoAuditoria.DENEGADO,
        )
        await session.commit()
    except Exception:  # la respuesta de seguridad no depende de la bitácora
        logger.exception("No se pudo auditar %s", accion)
        await session.rollback()


async def _rechazar_sesion(
    session: AsyncSession, request: Request | None, usuario_id: UUID | None, motivo: str, detalle: str
) -> HTTPException:
    await auditar_rechazo_acceso(
        session,
        request,
        accion="SESION_RECHAZADA",
        usuario_id=usuario_id,
        detalle={"motivo": motivo},
        clave=f"sesion_rechazada:{usuario_id}:{motivo}",
    )
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail=detalle, headers={"WWW-Authenticate": "Bearer"}
    )


async def _usuario_de_token_vigente(
    token: str, session: AsyncSession, jwt_handler: JWTHandler, request: Request | None = None
) -> Usuario:
    """Decodifica el token y comprueba que la sesión siga vigente.

    BE-009: la revocación vive en la base de datos (`jti_invalidados` y
    `users.tokens_invalidos_antes_de`): sobrevive a reinicios y vale igual en
    todas las instancias.

    HU-50 esc. 3: un token bien firmado que se rechaza (revocado, de un usuario
    desactivado o de una sesión invalidada) queda auditado como
    SESION_RECHAZADA con su usuario. Un token mal formado o caducado no se
    audita: no identifica a nadie y es el caso normal de una sesión vencida."""
    try:
        payload = jwt_handler.decodificar_token(token)
    except CredencialesInvalidasError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    repositorio = SQLAlchemyUsuarioRepository(session)
    # Revocación por jti: un token deslogueado deja de valer aunque no expire.
    if await SQLAlchemyJtiRepository(session).esta_invalidado(payload.jti):
        titular = await repositorio.obtener_por_id(payload.sub)
        raise await _rechazar_sesion(
            session, request, titular.id if titular else None, "token_revocado", "El token fue revocado"
        )

    usuario = await repositorio.obtener_por_id(payload.sub)
    if usuario is None:
        raise await _rechazar_sesion(session, request, None, "usuario_inexistente", "Usuario no encontrado")
    # HU-45: un token emitido antes de la desactivación deja de ser válido.
    if not usuario.is_active:
        raise await _rechazar_sesion(session, request, usuario.id, "usuario_desactivado", "Usuario desactivado")
    if not usuario.token_vigente(payload.iat):
        raise await _rechazar_sesion(
            session, request, usuario.id, "sesion_invalidada", "La sesión fue invalidada"
        )
    return usuario


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: DbSessionDep,
    jwt_handler: JWTHandlerDep,
    request: Request,
) -> Usuario:
    usuario = await _usuario_de_token_vigente(token, session, jwt_handler, request)
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
    request: Request,
) -> Usuario:
    return await _usuario_de_token_vigente(token, session, jwt_handler, request)


CurrentUserSinPrivacidadDep = Annotated[Usuario, Depends(get_current_user_sin_privacidad)]


def get_enviador_correo(settings: SettingsDep) -> EnviadorCorreo:
    """BE-025: sustituible en pruebas (no se sale a la red)."""
    return EnviadorCorreo(settings)


EnviadorCorreoDep = Annotated[EnviadorCorreo, Depends(get_enviador_correo)]


def servicio_codigos(session: AsyncSession, settings: Settings) -> ServicioCodigosVerificacion:
    """Códigos de verificación de un solo uso con la clave y la política de Settings.

    En producción sin CODIGOS_HMAC_KEY (solo admitido con el correo y el OTP
    apagados) no hay códigos: 503 en vez de un 500 o de una clave inventada."""
    try:
        clave = settings.clave_codigos
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Los códigos de verificación no están configurados. Contacte al administrador.",
        ) from exc
    return ServicioCodigosVerificacion(
        SQLAlchemyCodigoVerificacionRepository(session), clave, PoliticaCodigos.desde_settings(settings)
    )


def servicio_codigos_opcional(session: AsyncSession, settings: Settings) -> ServicioCodigosVerificacion | None:
    try:
        return servicio_codigos(session, settings)
    except HTTPException:
        return None


async def _auditar_denegacion_rbac(
    session: AsyncSession, request: Request, usuario: Usuario, roles_permitidos: tuple[Rol, ...]
) -> None:
    await auditar_rechazo_acceso(
        session,
        request,
        accion="RBAC_DENEGADO",
        usuario_id=usuario.id,
        detalle={"rol": usuario.rol.value, "roles_permitidos": sorted(r.value for r in roles_permitidos)},
        clave=f"rbac_denegado:{usuario.id}:{request.method}:{_ruta_de(request)}",
    )


def require_roles(*roles_permitidos: Rol) -> Callable[..., Awaitable[Usuario]]:
    """RBAC por ruta. HU-50 esc. 1: cada 403 queda auditado como RBAC_DENEGADO
    (con tope de volumen por usuario y ruta). `roles_permitidos` debe ser la
    única variable libre de `dependencia`: la matriz de HU-41 la lee de ahí."""

    async def dependencia(usuario: CurrentUserDep, session: DbSessionDep, request: Request) -> Usuario:
        try:
            verificar_permiso(usuario.rol, roles_permitidos)
        except PermisoDenegadoError as exc:
            await _auditar_denegacion_rbac(session, request, usuario, roles_permitidos)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        return usuario

    return dependencia
