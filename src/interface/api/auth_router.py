from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.autenticar_con_google import AutenticarConGoogleUseCase
from src.application.use_cases.autenticar_usuario import AutenticarUsuarioUseCase
from src.application.use_cases.gestionar_credenciales import (
    CambiarPasswordUseCase,
    RestablecerPasswordConEnlaceUseCase,
    SolicitarRecuperacionPasswordUseCase,
)
from src.application.use_cases.gestionar_privacidad import (
    AceptarPrivacidadUseCase,
    RechazarPrivacidadUseCase,
)
from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.exceptions import CredencialesInvalidasError, DomainError
from src.infrastructure.database.repositories.audit_log_repository import (
    SQLAlchemyAuditLogRepository,
)
from src.infrastructure.database.repositories.jti_repository import (
    MOTIVO_LOGOUT,
    MOTIVO_PASSWORD_CAMBIADA,
    MOTIVO_PRIVACIDAD_RECHAZADA,
    SQLAlchemyJtiRepository,
)
from src.infrastructure.database.repositories.password_reset_repository import (
    SQLAlchemyPasswordResetRepository,
)
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from src.infrastructure.security.google_verifier_provider import obtener_verificador_google
from src.infrastructure.security.privacidad import enmascarar_email
from src.interface.api.api_protection import limitar_por_ip
from src.interface.api.deps import (
    CurrentUserDep,
    CurrentUserSinPrivacidadDep,
    DbSessionDep,
    EnviadorCorreoDep,
    JWTHandlerDep,
    SettingsDep,
    oauth2_scheme,
)
from src.interface.api.schemas import (
    CambiarPasswordRequest,
    LoginGoogleRequest,
    PrivacidadResponse,
    RecuperarPasswordRequest,
    RestablecerPasswordRequest,
    SolicitudRecuperacionResponse,
    SSETicketResponse,
    TokenResponse,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _ip_cliente(request: Request) -> str:
    return request.client.host if request.client else "desconocida"


@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: DbSessionDep,
    jwt_handler: JWTHandlerDep,
    request: Request,
) -> TokenResponse:
    limiter = request.app.state.login_rate_limiter
    limiter_cuenta = request.app.state.login_cuenta_rate_limiter
    ip = _ip_cliente(request)
    cuenta = form_data.username.strip().lower()
    auditoria = AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    )
    # BE-008: la cuenta también tiene cuota propia. Mismo 429 y mismo mensaje
    # que el bloqueo por IP: la respuesta no revela si la cuenta existe.
    bloqueo_cuenta = limiter_cuenta.bloqueado(cuenta)
    if bloqueo_cuenta or limiter.bloqueado(ip):
        # HU-50 criterio 2: un evento administrativo DISTINTO de cada intento
        # fallido individual, visible en el panel de seguridad, sin afirmar
        # que se trata de un ataque confirmado — solo que se superó el umbral.
        await auditoria.execute(
            usuario_id=None,
            accion="UMBRAL_INTENTOS_FALLIDOS_EXCEDIDO",
            recurso="auth/login",
            detalle={
                "email": enmascarar_email(form_data.username),
                "alcance": "cuenta" if bloqueo_cuenta else "ip",
            },
            ip_origen=ip,
        )
        await session.commit()
        espera = max(limiter.segundos_para_reintentar(ip), limiter_cuenta.segundos_para_reintentar(cuenta))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos fallidos. Intenta nuevamente en unos minutos.",
            headers={"Retry-After": str(espera)},
        )

    usuario_repository = SQLAlchemyUsuarioRepository(session)
    use_case = AutenticarUsuarioUseCase(usuario_repository, jwt_handler)
    try:
        resultado = await use_case.execute(form_data.username, form_data.password)
    except CredencialesInvalidasError as exc:
        limiter.registrar_fallo(ip)
        limiter_cuenta.registrar_fallo(cuenta)
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
    limiter_cuenta.reiniciar(cuenta)
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
        require_password_change=resultado.require_password_change,
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
        require_password_change=resultado.require_password_change,
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
        ticket=jwt_handler.crear_ticket_sse(
            usuario.id, token_padre_jti=payload.jti, token_padre_iat=payload.iat, token_padre_exp=payload.exp
        )
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
    await SQLAlchemyJtiRepository(session).invalidar(payload.jti, payload.exp, MOTIVO_PRIVACIDAD_RECHAZADA)

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
    # BE-025: cerrar sesión siempre es posible, aunque falte el consentimiento
    # o haya una contraseña temporal pendiente de cambio.
    usuario: CurrentUserSinPrivacidadDep,
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
    # BE-009: persistente y común a todas las instancias del backend.
    await SQLAlchemyJtiRepository(session).invalidar(payload.jti, payload.exp, MOTIVO_LOGOUT)

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


# ── BE-025: ciclo de vida de la contraseña ────────────────────────────────


@router.post(
    "/cambiar-password",
    response_model=TokenResponse,
    dependencies=[limitar_por_ip("auth_cambiar_password", 10, 300)],
)
async def cambiar_password(
    body: CambiarPasswordRequest,
    usuario: CurrentUserSinPrivacidadDep,
    token: Annotated[str, Depends(oauth2_scheme)],
    session: DbSessionDep,
    jwt_handler: JWTHandlerDep,
    request: Request,
) -> TokenResponse:
    """Cambia la contraseña del usuario autenticado (también la temporal).

    Invalida todas las sesiones anteriores y devuelve un token nuevo para que
    quien la cambia no tenga que volver a iniciar sesión."""
    try:
        await CambiarPasswordUseCase(
            SQLAlchemyUsuarioRepository(session), SQLAlchemyPasswordResetRepository(session)
        ).execute(usuario, body.password_actual, body.password_nueva)
    except (CredencialesInvalidasError, DomainError) as exc:
        # 400 y no 401: la sesión es válida; lo incorrecto es el dato enviado.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    actual = jwt_handler.decodificar_token(token)
    await SQLAlchemyJtiRepository(session).invalidar(actual.jti, actual.exp, MOTIVO_PASSWORD_CAMBIADA)
    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=usuario.id,
        accion="PASSWORD_CAMBIADA",
        recurso="auth/cambiar-password",
        detalle=None,
        ip_origen=_ip_cliente(request),
    )
    await session.commit()
    return TokenResponse(
        access_token=jwt_handler.crear_token(usuario.id, usuario.email, usuario.rol),
        require_privacy_consent=usuario.requiere_aceptar_privacidad,
        require_password_change=False,
    )


_RESPUESTA_RECUPERACION = (
    "Si el correo corresponde a una cuenta activa, recibirás un enlace para restablecer la contraseña."
)


@router.post(
    "/recuperar-password",
    response_model=SolicitudRecuperacionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[limitar_por_ip("auth_recuperar_password", 5, 900)],
)
async def recuperar_password(
    body: RecuperarPasswordRequest,
    session: DbSessionDep,
    settings: SettingsDep,
    enviador: EnviadorCorreoDep,
    tareas: BackgroundTasks,
    request: Request,
) -> SolicitudRecuperacionResponse:
    """"Olvidé mi contraseña". La respuesta es idéntica exista o no la cuenta
    (no permite averiguar qué correos están registrados) y el correo se envía
    después de responder, para que el tiempo tampoco lo delate."""
    if not enviador.configurado:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La recuperación por correo no está disponible. Contacte al administrador.",
        )
    correo = await SolicitarRecuperacionPasswordUseCase(
        SQLAlchemyUsuarioRepository(session), SQLAlchemyPasswordResetRepository(session), settings.password_reset_url
    ).execute(body.email)
    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=None,
        accion="RECUPERACION_PASSWORD_SOLICITADA",
        recurso="auth/recuperar-password",
        detalle={"email": enmascarar_email(body.email)},
        ip_origen=_ip_cliente(request),
    )
    await session.commit()
    if correo is not None:
        tareas.add_task(enviador.enviar, correo.destino, correo.asunto, correo.cuerpo)
    return SolicitudRecuperacionResponse(detail=_RESPUESTA_RECUPERACION)


@router.post(
    "/restablecer-password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[limitar_por_ip("auth_restablecer_password", 10, 900)],
)
async def restablecer_password(
    body: RestablecerPasswordRequest,
    session: DbSessionDep,
    request: Request,
) -> Response:
    """Fija una contraseña nueva con el enlace de recuperación (un solo uso,
    30 minutos). Cierra todas las sesiones abiertas de la cuenta."""
    try:
        usuario = await RestablecerPasswordConEnlaceUseCase(
            SQLAlchemyUsuarioRepository(session), SQLAlchemyPasswordResetRepository(session)
        ).execute(body.token, body.password_nueva)
    except CredencialesInvalidasError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=usuario.id,
        accion="PASSWORD_RESTABLECIDA_CON_ENLACE",
        recurso="auth/restablecer-password",
        detalle=None,
        ip_origen=_ip_cliente(request),
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
