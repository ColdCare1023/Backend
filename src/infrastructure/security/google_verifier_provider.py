"""Proveedor cacheado del verificador de ID token de Google.

`GoogleTokenVerifier` mantiene el caché de claves públicas de Google (JWKS).
Construirlo en cada petición tiraría ese caché y obligaría a descargar el JWKS
en cada inicio de sesión, lo que además convertiría el endpoint en un
amplificador de tráfico hacia Google.

Vive en un módulo aparte del verificador para que las pruebas puedan sustituir
la implementación (`establecer_verificador_google`) sin tocar red ni necesitar
credenciales reales.
"""

from functools import lru_cache

from src.application.use_cases.autenticar_con_google import VerificadorTokenGoogle
from src.infrastructure.config import Settings
from src.infrastructure.security.google_token_verifier import GoogleTokenVerifier

_verificador_sustituto: VerificadorTokenGoogle | None = None


@lru_cache(maxsize=4)
def _construir(client_id: str) -> GoogleTokenVerifier:
    return GoogleTokenVerifier(client_id)


def obtener_verificador_google(settings: Settings) -> VerificadorTokenGoogle:
    if _verificador_sustituto is not None:
        return _verificador_sustituto
    return _construir(settings.google_client_id)


def establecer_verificador_google(verificador: VerificadorTokenGoogle | None) -> None:
    """Sustituye el verificador. Solo para pruebas: permite ejercitar el flujo
    completo (rate limiting, auditoría, allowlist) sin salir a Google."""
    global _verificador_sustituto
    _verificador_sustituto = verificador
