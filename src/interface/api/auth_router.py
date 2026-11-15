import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.autenticar_con_google import AutenticarConGoogleUseCase
from src.application.use_cases.autenticar_usuario import AutenticarUsuarioUseCase
from src.application.use_cases.gestionar_credenciales import (
    CODIGO_INVALIDO,
    CambiarPasswordUseCase,
    RestablecerPasswordConCodigoUseCase,
    RestablecerPasswordConEnlaceUseCase,
    SolicitarRecuperacionPasswordUseCase,
)
from src.application.use_cases.gestionar_privacidad import (
    AceptarPrivacidadUseCase,
    RechazarPrivacidadUseCase,
)
from src.application.use_cases.login_con_codigo import (
    DesafioLogin,
    IniciarLoginConCodigoUseCase,
    ReenviarCodigoLoginUseCase,
    VerificarCodigoLoginUseCase,
)
from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.exceptions import (
    CodigosBloqueadosError,
    CodigoVerificacionInvalidoError,
    CredencialesInvalidasError,
    DomainError,
    ReenvioPrematuroError,
)
from src.infrastructure.config import Settings
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
from src.infrastructure.notifications.plantillas_correo import url_logo
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
    servicio_codigos,
    servicio_codigos_opcional,
)
from src.interface.api.schemas import (
    CambiarPasswordRequest,
    LoginGoogleRequest,
    PrivacidadResponse,
    RecuperarPasswordRequest,
    ReenviarCodigoLoginRequest,
    RestablecerPasswordRequest,
    SolicitudRecuperacionResponse,
    SSETicketResponse,
    TokenResponse,
    VerificarCodigoLoginRequest,
)
from src.interface.api.sse_router import rechazar_si_flujos_agotados
from src.interface.limitador_auditoria import LIMITE_AUDITORIA

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)


def _ip_cliente(request: Request) -> str:
    return request.client.host if request.client else "desconocida"


def _auditoria(session: AsyncSession) -> AuditarAccionCriticaUseCase:
    return AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    )


_CORREO_NO_DISPONIBLE = (
    "El inicio de sesión requiere un código enviado por correo y el servicio de correo no está "
    "disponible en este momento. Contacte al administrador."
)
_CODIGO_NO_ENVIADO = "No se pudo enviar el código de verificación. Intenta nuevamente en unos minutos."
_DEMASIADOS_CODIGOS = "Se solicitaron demasiados códigos para esta cuenta. Intenta nuevamente en unos minutos."


def _respuesta_desafio(desafio: DesafioLogin) -> TokenResponse:
    return TokenResponse(
        access_token=None,
        requiere_codigo=True,
        desafio=desafio.desafio,
        email_enmascarado=desafio.email_enmascarado,
        expira_en_segundos=desafio.expira_en_segundos,
        reenviar_en_segundos=desafio.reenviar_en_segundos,
    )


async def _enviar_codigo_o_503(enviador, desafio: DesafioLogin) -> None:
    """El código del login se envía antes de responder: si el correo no sale,
    el usuario debe saberlo (no tiene otra forma de entrar)."""
    if not await enviador.enviar(desafio.destino, desafio.asunto, desafio.cuerpo, desafio.html):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_CODIGO_NO_ENVIADO)


def _429_codigos_bloqueados(exc: CodigosBloqueadosError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=str(exc),
        headers={"Retry-After": str(exc.segundos)},
    )


def _limitar_emision_codigo_login(request: Request, cuenta: str) -> None:
    limiter = request.app.state.codigo_login_emision_rate_limiter
    if limiter.bloqueado(cuenta):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=_DEMASIADOS_CODIGOS,
            headers={"Retry-After": str(limiter.segundos_para_reintentar(cuenta))},
        )
    limiter.registrar_fallo(cuenta)


@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: DbSessionDep,
    jwt_handler: JWTHandlerDep,
    settings: SettingsDep,
    enviador: EnviadorCorreoDep,
    request: Request,
) -> TokenResponse:
    """Login con correo y contraseña.

    Con OTP_LOGIN_ENABLED=false (por defecto) devuelve el token como siempre.
    Con OTP_LOGIN_ENABLED=true una contraseña correcta NO abre sesión: envía un
    código de 6 dígitos al correo y responde `requiere_codigo=true` con un
    `desafio` opaco que se canjea en POST /api/auth/login/verificar."""
    limiter = request.app.state.login_rate_limiter
    limiter_cuenta = request.app.state.login_cuenta_rate_limiter
    ip = _ip_cliente(request)
    cuenta = form_data.username.strip().lower()
    auditoria = _auditoria(session)
    # BE-008: la cuenta también tiene cuota propia. Mismo 429 y mismo mensaje
    # que el bloqueo por IP: la respuesta no revela si la cuenta existe.
    bloqueo_cuenta = limiter_cuenta.bloqueado(cuenta)
    if bloqueo_cuenta or limiter.bloqueado(ip):
        # HU-50 criterio 2: un evento administrativo DISTINTO de cada intento
        # fallido individual, visible en el panel de seguridad, sin afirmar
        # que se trata de un ataque confirmado — solo que se superó el umbral.
        # Esta rama no exige autenticación y la bitácora no admite borrado:
        # se audita una fila por ventana y clave bloqueada, con el número de
        # intentos que solo se contaron desde la anterior.
        alcance = "cuenta" if bloqueo_cuenta else "ip"
        sin_auditar = LIMITE_AUDITORIA.permitir(f"login_bloqueado:{alcance}:{cuenta if bloqueo_cuenta else ip}")
        if sin_auditar is not None:
            # HU-50 esc. 3: el bloqueo de una cuenta existente se correlaciona por actor.
            titular = (
                await SQLAlchemyUsuarioRepository(session).obtener_por_email(form_data.username.strip())
                if bloqueo_cuenta
                else None
            )
            await auditoria.execute(
                usuario_id=titular.id if titular is not None else None,
                accion="UMBRAL_INTENTOS_FALLIDOS_EXCEDIDO",
                recurso="auth/login",
                detalle={
                    "email": enmascarar_email(form_data.username),
                    "alcance": alcance,
                    "intentos_sin_auditar": sin_auditar,
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

    # Fallar cerrado: con el segundo factor activo y sin correo nadie recibiría
    # el código. Se responde antes de mirar la contraseña para no revelar si era
    # correcta.
    if settings.otp_login_enabled and not enviador.configurado:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_CORREO_NO_DISPONIBLE)

    usuario_repository = SQLAlchemyUsuarioRepository(session)
    use_case = AutenticarUsuarioUseCase(usuario_repository, jwt_handler)
    try:
        # `cuenta` (en minúsculas) es solo la clave de los limitadores: el
        # repositorio recibe el correo tal cual lo escribió el titular.
        usuario = await use_case.verificar_credenciales(form_data.username, form_data.password)
    except CredencialesInvalidasError as exc:
        limiter.registrar_fallo(ip)
        limiter_cuenta.registrar_fallo(cuenta)
        # HU-50 esc. 1 y 3: con la cuenta existente, el intento queda a nombre
        # de su usuario (filtrable por actor); la respuesta sigue siendo genérica.
        await auditoria.execute(
            usuario_id=exc.usuario_id,
            accion="LOGIN_FALLIDO",
            recurso="auth/login",
            detalle={"email": enmascarar_email(form_data.username), "motivo": exc.motivo},
            ip_origen=ip,
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if settings.otp_login_enabled:
        # Los contadores de fallos se reinician recién al verificar el código:
        # una contraseña correcta todavía no es un inicio de sesión.
        _limitar_emision_codigo_login(request, cuenta)
        try:
            desafio = await IniciarLoginConCodigoUseCase(
                servicio_codigos(session, settings), url_logo(settings.base_frontend), settings.base_frontend
            ).execute(usuario, ip)
        except CodigosBloqueadosError as exc:
            raise _429_codigos_bloqueados(exc) from exc
        await auditoria.execute(
            usuario_id=usuario.id,
            accion="LOGIN_CODIGO_EMITIDO",
            recurso="auth/login",
            detalle={"metodo": "password", "email": desafio.email_enmascarado},
            ip_origen=ip,
        )
        await session.commit()
        await _enviar_codigo_o_503(enviador, desafio)
        return _respuesta_desafio(desafio)

    resultado = use_case.emitir_sesion(usuario)
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


@router.post(
    "/login/verificar",
    response_model=TokenResponse,
    dependencies=[limitar_por_ip("auth_login_verificar", 20, 300)],
)
async def verificar_codigo_login(
    body: VerificarCodigoLoginRequest,
    session: DbSessionDep,
    jwt_handler: JWTHandlerDep,
    settings: SettingsDep,
    request: Request,
) -> TokenResponse:
    """Segundo paso del login con código: canjea `desafio` + `codigo` por el
    token de acceso. Cada fallo cuenta un intento en la base (al quinto el
    código se quema) y otro en la cuota por cuenta del login."""
    ip = _ip_cliente(request)
    limiter = request.app.state.login_rate_limiter
    limiter_cuenta = request.app.state.login_cuenta_rate_limiter
    auditoria = _auditoria(session)
    usuarios = SQLAlchemyUsuarioRepository(session)
    use_case = VerificarCodigoLoginUseCase(servicio_codigos(session, settings), usuarios)

    registro, usuario = await use_case.localizar(body.desafio)
    cuenta = usuario.email.strip().lower() if usuario is not None else None
    if cuenta is not None and limiter_cuenta.bloqueado(cuenta):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos fallidos. Intenta nuevamente en unos minutos.",
            headers={"Retry-After": str(limiter_cuenta.segundos_para_reintentar(cuenta))},
        )

    resultado = await use_case.execute(registro, usuario, body.codigo) if registro is not None else None
    if resultado is None or not resultado.valido:
        if cuenta is not None:
            limiter_cuenta.registrar_fallo(cuenta)
        await auditoria.execute(
            usuario_id=registro.usuario_id if registro is not None else None,
            accion="LOGIN_CODIGO_FALLIDO",
            recurso="auth/login/verificar",
            detalle={
                "motivo": resultado.verificacion.estado.value if resultado else "desafio_invalido",
                "intentos_restantes": resultado.verificacion.intentos_restantes if resultado else 0,
            },
            ip_origen=ip,
        )
        await session.commit()
        mensaje = resultado.mensaje if resultado else "La verificación caducó o no es válida. Vuelve a iniciar sesión."
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=mensaje, headers={"WWW-Authenticate": "Bearer"}
        )

    assert usuario is not None and cuenta is not None
    sesion = AutenticarUsuarioUseCase(usuarios, jwt_handler).emitir_sesion(usuario)
    limiter.reiniciar(ip)
    limiter_cuenta.reiniciar(cuenta)
    request.app.state.codigo_login_emision_rate_limiter.reiniciar(cuenta)
    await auditoria.execute(
        usuario_id=usuario.id,
        accion="LOGIN_EXITOSO",
        recurso="auth/login",
        detalle={"metodo": "password+codigo"},
        ip_origen=ip,
    )
    await session.commit()
    return TokenResponse(
        access_token=sesion.access_token,
        require_privacy_consent=sesion.require_privacy_consent,
        require_password_change=sesion.require_password_change,
    )


@router.post(
    "/login/reenviar",
    response_model=TokenResponse,
    dependencies=[limitar_por_ip("auth_login_reenviar", 10, 300)],
)
async def reenviar_codigo_login(
    body: ReenviarCodigoLoginRequest,
    session: DbSessionDep,
    settings: SettingsDep,
    enviador: EnviadorCorreoDep,
    request: Request,
) -> TokenResponse:
    """Código nuevo para el mismo login pendiente (mismo `desafio`). Respeta la
    espera CODIGO_REENVIO_SEGUNDOS (429 con Retry-After) y conserva los
    intentos fallidos ya acumulados."""
    if not enviador.configurado:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_CORREO_NO_DISPONIBLE)
    use_case = ReenviarCodigoLoginUseCase(
        servicio_codigos(session, settings),
        SQLAlchemyUsuarioRepository(session),
        url_logo(settings.base_frontend),
        settings.base_frontend,
    )
    try:
        registro, usuario = await use_case.localizar(body.desafio)
    except CodigoVerificacionInvalidoError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    cuenta = usuario.email.strip().lower()
    try:
        # La espera se comprueba antes de contar el envío en la cuota por cuenta.
        espera = use_case.segundos_para_reenviar(registro)
        if espera > 0:
            raise ReenvioPrematuroError(espera)
        _limitar_emision_codigo_login(request, cuenta)
        desafio = await use_case.execute(registro, usuario, body.desafio)
    except ReenvioPrematuroError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.segundos)},
        ) from exc
    except CodigosBloqueadosError as exc:
        raise _429_codigos_bloqueados(exc) from exc
    await _auditoria(session).execute(
        usuario_id=usuario.id,
        accion="LOGIN_CODIGO_REENVIADO",
        recurso="auth/login/reenviar",
        detalle={"email": desafio.email_enmascarado},
        ip_origen=_ip_cliente(request),
    )
    await session.commit()
    await _enviar_codigo_o_503(enviador, desafio)
    return _respuesta_desafio(desafio)


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

    No pide el código por correo de OTP_LOGIN_ENABLED: la identidad ya la
    verificó Google (con su propio segundo factor, si el titular lo tiene), y
    el código solo protege el login con contraseña frente a una contraseña
    filtrada o adivinada.

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
            usuario_id=exc.usuario_id,
            accion="LOGIN_GOOGLE_FALLIDO",
            recurso="auth/google",
            detalle={"motivo": "identidad no autorizada o token inválido", "causa": exc.motivo},
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


@router.post(
    "/sse-ticket",
    response_model=SSETicketResponse,
    responses={429: {"description": "La cuenta ya tiene abiertos los flujos SSE permitidos (`demasiados_flujos`)."}},
)
async def emitir_ticket_sse(
    request: Request,
    usuario: CurrentUserDep,
    token: Annotated[str, Depends(oauth2_scheme)],
    jwt_handler: JWTHandlerDep,
    settings: SettingsDep,
) -> SSETicketResponse:
    """Ticket efímero para abrir el stream SSE (EventSource no envía headers).

    El ticket queda atado al access token que lo pidió: al cerrar sesión, el
    stream que se abrió con él deja de emitir.

    HU-32 esc. 3: con la cuenta en su tope de flujos simultáneos
    (SSE_MAX_STREAMS_POR_USUARIO) responde 429 `demasiados_flujos` aquí, donde
    el frontend puede leerlo, en vez de emitir un ticket que el stream rechazaría.
    """
    rechazar_si_flujos_agotados(
        request.app.state.sse_streams_por_usuario, usuario.id, settings.sse_max_streams_por_usuario
    )
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
    settings: SettingsDep,
    request: Request,
) -> TokenResponse:
    """Cambia la contraseña del usuario autenticado (también la temporal).

    Invalida todas las sesiones anteriores y devuelve un token nuevo para que
    quien la cambia no tenga que volver a iniciar sesión. Anula los enlaces y
    los códigos pendientes (p. ej. el de activación de 48 h)."""
    try:
        await CambiarPasswordUseCase(
            SQLAlchemyUsuarioRepository(session),
            SQLAlchemyPasswordResetRepository(session),
            servicio_codigos_opcional(session, settings),
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
    "Si el correo corresponde a una cuenta activa, recibirás un código de verificación "
    "para restablecer la contraseña."
)


async def _procesar_recuperacion(
    fabrica_sesiones: async_sessionmaker[AsyncSession],
    settings: Settings,
    enviador,
    email: str,
    ip: str,
) -> None:
    """Después de responder: busca la cuenta, emite código y enlace y envía el
    correo. Fuera de la petición, así el tiempo de respuesta es el mismo exista
    o no la cuenta."""
    try:
        async with fabrica_sesiones() as session:
            correo = await SolicitarRecuperacionPasswordUseCase(
                SQLAlchemyUsuarioRepository(session),
                SQLAlchemyPasswordResetRepository(session),
                settings.url_restablecer,
                codigos=servicio_codigos(session, settings),
                url_codigo=settings.url_codigo_cuenta,
                ip=ip,
            ).execute(email)
            await session.commit()
    except Exception:  # una tarea en segundo plano no debe romper el worker
        # Sin el correo ni el detalle: podría citar al destinatario.
        logger.error("No se pudo procesar una solicitud de recuperación de contraseña")
        return
    if correo is not None:
        await enviador.enviar(correo.destino, correo.asunto, correo.cuerpo, correo.html)


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
    (no permite averiguar qué correos están registrados). Antes de responder
    solo se aplican las cuotas y se audita la solicitud; buscar la cuenta,
    emitir el código y enviar el correo ocurre después de responder, para que
    el tiempo tampoco lo delate.

    El correo lleva un código de 6 dígitos (y el enlace de un solo uso de
    BE-025, que sigue funcionando). Además de la cuota por IP hay una por
    cuenta (3 cada 15 min) y el tope de fallos con códigos por cuenta: pasados,
    se responde igual pero no se envía nada."""
    if not enviador.configurado:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La recuperación por correo no está disponible. Contacte al administrador.",
        )
    servicio_codigos(session, settings)  # 503 ahora, no en segundo plano, si faltara la clave
    ip = _ip_cliente(request)
    email = str(body.email).strip()
    cuenta = email.lower()
    limiter_cuenta = request.app.state.recuperacion_cuenta_rate_limiter
    limitada = limiter_cuenta.bloqueado(cuenta)
    detalle: dict[str, object] = {"email": enmascarar_email(cuenta)}
    if limitada:
        detalle["limitada_por_cuenta"] = True
    else:
        limiter_cuenta.registrar_fallo(cuenta)
    await _auditoria(session).execute(
        usuario_id=None,
        accion="RECUPERACION_PASSWORD_SOLICITADA",
        recurso="auth/recuperar-password",
        detalle=detalle,
        ip_origen=ip,
    )
    await session.commit()
    if not limitada:
        tareas.add_task(
            _procesar_recuperacion,
            async_sessionmaker(session.bind, expire_on_commit=False),
            settings,
            enviador,
            email,
            ip,
        )
    return SolicitudRecuperacionResponse(detail=_RESPUESTA_RECUPERACION)


@router.post(
    "/restablecer-password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[limitar_por_ip("auth_restablecer_password", 10, 900)],
)
async def restablecer_password(
    body: RestablecerPasswordRequest,
    session: DbSessionDep,
    settings: SettingsDep,
    request: Request,
) -> Response:
    """Fija una contraseña nueva. Dos formas:

    - `{email, codigo, password_nueva}`: código de recuperación (10 min) o de
      activación (48 h) recibido por correo;
    - `{token, password_nueva}`: enlace de recuperación de BE-025 (30 min).

    Ambas son de un solo uso, dejan la contraseña como propia (no temporal),
    cierran todas las sesiones abiertas y anulan enlaces y códigos pendientes."""
    ip = _ip_cliente(request)
    usuarios = SQLAlchemyUsuarioRepository(session)
    recuperacion = SQLAlchemyPasswordResetRepository(session)

    if body.token is not None:
        try:
            usuario = await RestablecerPasswordConEnlaceUseCase(
                usuarios, recuperacion, servicio_codigos_opcional(session, settings)
            ).execute(
                body.token, body.password_nueva
            )
        except CredencialesInvalidasError as exc:
            await session.rollback()
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        accion, detalle = "PASSWORD_RESTABLECIDA_CON_ENLACE", None
    else:
        assert body.email is not None and body.codigo is not None
        email = str(body.email).strip()
        cuenta = email.lower()  # clave del limitador; el repositorio recibe el correo tal cual
        limiter_cuenta = request.app.state.login_cuenta_rate_limiter
        if limiter_cuenta.bloqueado(cuenta):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Demasiados intentos fallidos. Intenta nuevamente en unos minutos.",
                headers={"Retry-After": str(limiter_cuenta.segundos_para_reintentar(cuenta))},
            )
        codigos = servicio_codigos(session, settings)
        try:
            resultado = await RestablecerPasswordConCodigoUseCase(usuarios, recuperacion, codigos).execute(
                email, body.codigo, body.password_nueva
            )
        except CodigoVerificacionInvalidoError as exc:
            limiter_cuenta.registrar_fallo(cuenta)
            await _auditoria(session).execute(
                usuario_id=exc.usuario_id,
                accion="RESTABLECER_CODIGO_FALLIDO",
                recurso="auth/restablecer-password",
                detalle={"email": enmascarar_email(cuenta), "motivo": exc.motivo},
                ip_origen=ip,
            )
            # Se confirma: el intento fallido debe quedar contado en la base.
            await session.commit()
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=CODIGO_INVALIDO) from exc
        usuario = resultado.usuario
        limiter_cuenta.reiniciar(cuenta)
        accion, detalle = "PASSWORD_RESTABLECIDA", {"metodo": "codigo", "proposito": resultado.proposito.value}

    await _auditoria(session).execute(
        usuario_id=usuario.id,
        accion=accion,
        recurso="auth/restablecer-password",
        detalle=detalle,
        ip_origen=ip,
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
