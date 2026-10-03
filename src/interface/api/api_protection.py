from collections.abc import Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from src.infrastructure.config import Settings
from src.infrastructure.security.rate_limiter import SlidingWindowRateLimiter
from src.interface.api.deps import CurrentUserDep, SettingsDep

# Los probes de disponibilidad de la plataforma no consumen cuota.
_RUTAS_EXENTAS = ("/health",)


class _CuerpoDemasiadoGrande(Exception):
    pass


class LimiteCuerpoASGI:
    """Cuenta bytes realmente recibidos, incluso con transferencias chunked.

    ``Content-Length`` solo permite rechazar antes. Este límite cubre el caso
    en que el cliente omite o miente en esa cabecera antes de que FastAPI
    deserialice el cuerpo.
    """

    def __init__(self, app, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        recibidos = 0
        respuesta_iniciada = False

        async def receive_limitado():
            nonlocal recibidos
            message = await receive()
            if message["type"] == "http.request":
                recibidos += len(message.get("body", b""))
                if recibidos > self.max_bytes:
                    raise _CuerpoDemasiadoGrande
            return message

        async def send_observado(message):
            nonlocal respuesta_iniciada
            if message["type"] == "http.response.start":
                respuesta_iniciada = True
            await send(message)

        try:
            await self.app(scope, receive_limitado, send_observado)
        except _CuerpoDemasiadoGrande:
            if not respuesta_iniciada:
                await JSONResponse(
                    status_code=413,
                    content={"detail": "Cuerpo de la solicitud demasiado grande."},
                )(scope, receive, send)


def instalar_proteccion_api(app: FastAPI, settings: Settings) -> None:
    """Controles de disponibilidad de capa aplicación (OWASP API4:2023):

    - Límite global de solicitudes por IP con ventana deslizante. A diferencia
      del limitador de login (que solo cuenta fallos), aquí cuenta toda
      solicitud: su objetivo es absorber scraping/flooding, no fuerza bruta.
    - Límite de tamaño del cuerpo: los payloads legítimos del sistema (lecturas
      IoT, formularios) son de unos pocos KB; se rechaza temprano lo demás.
    """

    app.add_middleware(LimiteCuerpoASGI, max_bytes=settings.max_body_bytes)
    limiter = SlidingWindowRateLimiter(
        max_intentos=settings.api_rate_limit_max_solicitudes,
        ventana_segundos=settings.api_rate_limit_ventana_segundos,
        max_claves=settings.security_state_max_entries,
    )
    app.state.api_rate_limiter = limiter
    # Cuotas por endpoint (ver `limitar_por_ip` / `limitar_por_usuario`), que se
    # crean en la primera solicitud a cada ruta protegida.
    app.state.limitadores_endpoint = {}

    @app.middleware("http")
    async def proteccion_api(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path in _RUTAS_EXENTAS:
            return await call_next(request)

        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                longitud = int(content_length)
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Content-Length inválido."},
                )
            if longitud < 0:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Content-Length inválido."},
                )
            if longitud > settings.max_body_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Cuerpo de la solicitud demasiado grande."},
                )

        if settings.api_rate_limit_habilitado:
            ip = request.client.host if request.client else "desconocida"
            if limiter.bloqueado(ip):
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Demasiadas solicitudes. Intenta nuevamente en unos segundos."},
                    headers={"Retry-After": str(limiter.segundos_para_reintentar(ip))},
                )
            limiter.registrar_fallo(ip)  # aquí cada solicitud cuenta para la ventana

        return await call_next(request)


# ── Límites por endpoint (B13) ────────────────────────────────────────────
#
# El límite global por IP (240 req/min) absorbe scraping y flooding genéricos,
# pero trata igual a un GET barato que a una verificación de cadena que recorre
# todos los registros. Estos límites cubren el hueco.
#
# No ponen en riesgo el volcado del buffer del ESP32 (RNF-07, sincronización
# ≤30 s tras reconectar): ese reenvío viaja por MQTT (RF-05/RF-06) y no
# atraviesa la pila HTTP. El endpoint REST de ingesta es la vía secundaria.


def _limitador(request: Request, settings: Settings, nombre: str, max_solicitudes: int,
               ventana_segundos: int) -> SlidingWindowRateLimiter:
    """Un limitador por endpoint, vivo en el estado de la app.

    En `app.state` y no a nivel de módulo a propósito: cada instancia de la
    aplicación (incluida cada prueba) arranca con la cuota limpia, y un límite
    alcanzado en una prueba no puede filtrarse a la siguiente.
    """
    registro = request.app.state.limitadores_endpoint
    limitador = registro.get(nombre)
    if limitador is None:
        limitador = SlidingWindowRateLimiter(
            max_intentos=max_solicitudes,
            ventana_segundos=ventana_segundos,
            max_claves=settings.security_state_max_entries,
        )
        registro[nombre] = limitador
    return limitador


def _aplicar(request: Request, settings: Settings, nombre: str, clave: str,
             max_solicitudes: int, ventana_segundos: int) -> None:
    if not settings.api_rate_limit_habilitado:
        return
    limitador = _limitador(request, settings, nombre, max_solicitudes, ventana_segundos)
    if limitador.bloqueado(clave):
        # No se registra el intento bloqueado: contarlo extendería la espera en
        # cada reintento y el cliente nunca saldría de la ventana.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiadas solicitudes a este recurso. Intenta nuevamente en unos segundos.",
            headers={"Retry-After": str(limitador.segundos_para_reintentar(clave))},
        )
    limitador.registrar_fallo(clave)


def limitar_por_ip(nombre: str, max_solicitudes: int, ventana_segundos: int):
    """Dependencia de cuota por IP para un endpoint concreto."""

    def dependencia(request: Request, settings: SettingsDep) -> None:
        ip = request.client.host if request.client else "desconocida"
        _aplicar(request, settings, nombre, ip, max_solicitudes, ventana_segundos)

    return Depends(dependencia)


def limitar_por_usuario(nombre: str, max_solicitudes: int, ventana_segundos: int):
    """Cuota por usuario autenticado.

    Para operaciones caras la IP es la clave equivocada: varios usuarios de la
    misma farmacia comparten salida a internet y uno solo agotaría la cuota de
    todos.
    """

    def dependencia(request: Request, settings: SettingsDep, usuario: CurrentUserDep) -> None:
        _aplicar(request, settings, nombre, str(usuario.id), max_solicitudes, ventana_segundos)

    return Depends(dependencia)


def limitar_ingesta_lecturas():
    """Cuota de la ingesta REST de lecturas, configurable por entorno.

    A diferencia de las otras, sus valores salen de `Settings` y no de
    constantes: el caudal aceptable depende del parque de dispositivos
    desplegado, y ajustarlo no debería exigir tocar el código.
    """

    def dependencia(request: Request, settings: SettingsDep) -> None:
        ip = request.client.host if request.client else "desconocida"
        _aplicar(
            request,
            settings,
            "lecturas_ingesta",
            ip,
            settings.ingesta_rate_limit_max_solicitudes,
            settings.ingesta_rate_limit_ventana_segundos,
        )

    return Depends(dependencia)
