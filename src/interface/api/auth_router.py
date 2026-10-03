from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.autenticar_con_google import AutenticarConGoogleUseCase
from src.application.use_cases.autenticar_usuario import AutenticarUsuarioUseCase
from src.application.use_cases.gestionar_privacidad import (
    AceptarPrivacidadUseCase,
    RechazarPrivacidadUseCase,
)
from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.exceptions import CredencialesInvalidasError
from src.infrastructure.database.repositories.audit_log_repository import (
    SQLAlchemyAuditLogRepository,
)
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from src.infrastructure.security.google_verifier_provider import obtener_verificador_google
from src.interface.api.deps import (
    CurrentUserDep,
    CurrentUserSinPrivacidadDep,
    DbSessionDep,
    JWTHandlerDep,
    SettingsDep,
    oauth2_scheme,
)
from src.interface.api.schemas import (
    LoginGoogleRequest,
    PrivacidadResponse,
    SSETicketResponse,
    TokenResponse,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _ip_cliente(request: Request) -> str:
    return request.client.host if request.client else "desconocida"


def enmascarar_email(email: str) -> str:
    """Reduce un correo a `p***@dominio` para la bitácora.

    Ley N.° 29733, principio de proporcionalidad: el correo tecleado en un
    intento FALLIDO puede no pertenecer a ningún usuario del sistema —un error
    de escritura, o la dirección de un tercero usada por quien ataca—, y
    `audit_logs` es inmutable por diseño, así que no hay vía de rectificación
    ni de supresión posterior.

    Se conserva lo que da valor forense a RF-16: la inicial y el dominio bastan
    para distinguir un ataque dirigido a una cuenta concreta de un barrido, y
    para correlacionar intentos (el mismo correo produce siempre la misma
    máscara). Lo que se descarta es la identificación directa del titular.
    """
    if not email or "@" not in email:
        # Sin forma de correo no hay dominio que preservar; se guarda solo la
        # longitud, que sigue permitiendo distinguir intentos entre sí.
        return f"<sin formato de correo, {len(email)} caracteres>"
    local, _, dominio = email.partition("@")
    inicial = local[0] if local else ""
    return f"{inicial}***@{dominio}"


@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: DbSessionDep,
    jwt_handler: JWTHandlerDep,
    request: Request,
) -> TokenResponse:
    limiter = request.app.state.login_rate_limiter
    ip = _ip_cliente(request)
    auditoria = AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    )
    if limiter.bloqueado(ip):
        # HU-50 criterio 2: un evento administrativo DISTINTO de cada intento
        # fallido individual, visible en el panel de seguridad, sin afirmar
        # que se trata de un ataque confirmado — solo que se superó el umbral.
        await auditoria.execute(
            usuario_id=None,
            accion="UMBRAL_INTENTOS_FALLIDOS_EXCEDIDO",
            recurso="auth/login",
            detalle={"email": enmascarar_email(form_data.username)},
            ip_origen=ip,
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos fallidos. Intenta nuevamente en unos minutos.",
            headers={"Retry-After": str(limiter.segundos_para_reintentar(ip))},
        )

    usuario_repository = SQLAlchemyUsuarioRepository(session)
    use_case = AutenticarUsuarioUseCase(usuario_repository, jwt_handler)
    try:
        resultado = await use_case.execute(form_data.username, form_data.password)
    except CredencialesInvalidasError as exc:
        limiter.registrar_fallo(ip)
        await auditoria.execute(
            usuario_id=None,
            accion="LOGIN_FALLIDO",
            recurso="auth/login",
            detalle={"email": enmascarar_email(form_data.username)},
            ip_origen=ip,
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    limiter.reiniciar(ip)
    await auditoria.execute(
        usuario_id=resultado.usuario_id,
        accion="LOGIN_EXITOSO",
        recurso="auth/login",
        # En un login correcto `usuario_id` ya identifica al titular de forma
        # estable; repetir el correo sería dato redundante en una bitácora
        # inmutable. Se deja constancia del método de acceso, como en /google.
        detalle={"metodo": "password"},
        ip_origen=ip,
    )
    await session.commit()
    return TokenResponse(
        access_token=resultado.access_token,
        require_privacy_consent=resultado.require_privacy_consent,
    )


@router.post("/google", response_model=TokenResponse)
async def login_google(
    body: LoginGoogleRequest,
    session: DbSessionDep,
    jwt_handler: JWTHandlerDep,
    settings: SettingsDep,
    request: Request,
) -> TokenResponse:
    """RF-17 (método alternativo): Google verifica la identidad; la
    autorización sigue siendo la tabla `users`.

    No da de alta usuarios. Un correo que no esté ya provisionado por un
    administrador se rechaza con el mismo mensaje que un token inválido, de
    modo que el endpoint no sirva para averiguar qué correos tienen acceso.
    """
    if not settings.google_oauth_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El acceso con Google no está habilitado.",
        )

    # Misma cuota que el login con contraseña: sin ella, este endpoint sería el
    # camino sin límite para sondear qué correos están dados de alta.
    limiter = request.app.state.login_rate_limiter
    ip = _ip_cliente(request)
    if limiter.bloqueado(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos fallidos. Intenta nuevamente en unos minutos.",
            headers={"Retry-After": str(limiter.segundos_para_reintentar(ip))},
        )

    auditoria = AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    )
    use_case = AutenticarConGoogleUseCase(
        SQLAlchemyUsuarioRepository(session),
        jwt_handler,
        obtener_verificador_google(settings),
    )
    try:
        resultado = await use_case.execute(body.id_token)
    except CredencialesInvalidasError as exc:
        limiter.registrar_fallo(ip)
        await auditoria.execute(
            usuario_id=None,
            accion="LOGIN_GOOGLE_FALLIDO",
            recurso="auth/google",
            detalle={"motivo": "identidad no autorizada o token inválido"},
            ip_origen=ip,
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    limiter.reiniciar(ip)
    await auditoria.execute(
        usuario_id=resultado.usuario_id,
        accion="LOGIN_EXITOSO",
        recurso="auth/google",
        # Queda constancia del método de acceso: la bitácora debe permitir
        # distinguir una sesión abierta con contraseña de una abierta con Google.
        detalle={"metodo": "google"},
        ip_origen=ip,
    )
    await session.commit()
    return TokenResponse(
        access_token=resultado.access_token,
        require_privacy_consent=resultado.require_privacy_consent,
    )


@router.post("/sse-ticket", response_model=SSETicketResponse)
async def emitir_ticket_sse(
    usuario: CurrentUserDep,
    token: Annotated[str, Depends(oauth2_scheme)],
    jwt_handler: JWTHandlerDep,
) -> SSETicketResponse:
    """Ticket efímero para abrir el stream SSE (EventSource no envía headers).

    El ticket queda atado al access token que lo pidió: al cerrar sesión, el
    stream que se abrió con él deja de emitir.
    """
    payload = jwt_handler.decodificar_token(token)
    return SSETicketResponse(
        ticket=jwt_handler.crear_ticket_sse(usuario.id, token_padre_jti=payload.jti)
    )


@router.post("/privacidad/aceptar", response_model=PrivacidadResponse)
async def aceptar_privacidad(
    usuario: CurrentUserSinPrivacidadDep,
    session: DbSessionDep,
    request: Request,
) -> PrivacidadResponse:
    """HU-44 Escenario 2: consentimiento explícito de la Ley N.° 29733."""
    use_case = AceptarPrivacidadUseCase(
        SQLAlchemyUsuarioRepository(session),
        RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session)),
    )
    actualizado = await use_case.execute(usuario, _ip_cliente(request))
    await session.commit()
    return PrivacidadResponse(
        privacy_accepted=actualizado.privacy_accepted,
        privacy_version_accepted=actualizado.privacy_version_accepted,
    )


@router.post("/privacidad/rechazar", status_code=status.HTTP_401_UNAUTHORIZED)
async def rechazar_privacidad(
    usuario: CurrentUserSinPrivacidadDep,
    token: Annotated[str, Depends(oauth2_scheme)],
    session: DbSessionDep,
    jwt_handler: JWTHandlerDep,
    request: Request,
) -> None:
    """HU-44 Escenario 3: rechazar revoca el token — sin política aceptada no hay sesión."""
    payload = jwt_handler.decodificar_token(token)
    request.app.state.token_revocation.registrar(payload.jti, payload.exp.timestamp())

    use_case = RechazarPrivacidadUseCase(
        RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session))
    )
    await use_case.execute(usuario, _ip_cliente(request))
    await session.commit()
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No se puede continuar sin aceptar la política de privacidad",
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    usuario: CurrentUserDep,
    token: Annotated[str, Depends(oauth2_scheme)],
    session: DbSessionDep,
    jwt_handler: JWTHandlerDep,
    request: Request,
) -> Response:
    """Revoca el access token actual (por jti) hasta su expiración natural.

    Sin esto, "cerrar sesión" solo borra el token del cliente; el token seguiría
    siendo válido si fue copiado. La revocación server-side cierra esa ventana.
    """
    payload = jwt_handler.decodificar_token(token)
    request.app.state.token_revocation.registrar(payload.jti, payload.exp.timestamp())

    auditoria = AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    )
    await auditoria.execute(
        usuario_id=usuario.id,
        accion="LOGOUT",
        recurso="auth/logout",
        detalle=None,
        ip_origen=_ip_cliente(request),
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
