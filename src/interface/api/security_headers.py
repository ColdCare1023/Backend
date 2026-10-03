from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

# La documentación interactiva (Swagger UI / ReDoc) necesita cargar sus
# propios scripts; el resto de la API es JSON puro y no ejecuta nada.
_RUTAS_DOCS = ("/docs", "/redoc", "/openapi.json")

_CSP_API = "default-src 'none'; frame-ancestors 'none'"


def instalar_security_headers(app: FastAPI, *, hsts: bool) -> None:
    """Cabeceras de seguridad OWASP en toda respuesta de la API.

    HSTS solo se emite en producción (detrás de TLS); emitirlo en desarrollo
    sobre http://localhost lo dejaría cacheado sin efecto útil.
    """

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=()"
        if not request.url.path.startswith(_RUTAS_DOCS):
            response.headers["Content-Security-Policy"] = _CSP_API
        if request.url.path.startswith("/api"):
            # Tokens y credenciales jamás deben quedar en cachés intermedias, y
            # tampoco los datos personales y sanitarios que devuelve el resto de
            # la API (historial, auditoría, reportes BPA): la Ley N.° 29733 exige
            # limitar su difusión, y una caché intermedia es difusión no prevista.
            response.headers["Cache-Control"] = "no-store"
        if hsts:
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains"
            )
        return response
