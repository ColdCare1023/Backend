"""Caminos de rechazo de la dependencia de autenticación (RNF-06).

Todos comparten la misma exigencia: presentar un JWT criptográficamente válido
NO basta. El estado del usuario en la base de datos manda sobre lo que diga el
token, porque el token es un dato del pasado.
"""

from uuid import uuid4

from src.domain.value_objects.rol import Rol
from src.infrastructure.config import Settings
from src.infrastructure.security.jwt_handler import JWTHandler
from tests.conftest import auth_header


def _handler() -> JWTHandler:
    """Mismo secreto que usa la aplicación bajo prueba (ver conftest)."""
    return JWTHandler(Settings())


async def test_token_valido_de_un_usuario_inexistente_se_rechaza(client):
    """Firma correcta pero `sub` que no existe: p. ej. un token emitido antes
    de purgar la cuenta. Debe ser 401, no un 500 por usuario None."""
    token = _handler().crear_token(uuid4(), "fantasma@farmacia.example.org", Rol.ADMINISTRADOR)

    response = client.get("/api/usuarios", headers=auth_header(token))

    assert response.status_code == 401
    assert "no encontrado" in response.json()["detail"].lower()


async def test_token_sin_los_claims_obligatorios_se_rechaza(client):
    """El token se firma con el secreto correcto pero le faltan iss/aud/jti:
    aceptarlo permitiría reutilizar tokens de otro sistema que compartiera
    secreto, o tokens no revocables (sin jti)."""
    import jwt as pyjwt

    settings = Settings()
    token = pyjwt.encode({"sub": str(uuid4())}, settings.jwt_secret_key, algorithm="HS256")

    response = client.get("/api/usuarios", headers=auth_header(token))

    assert response.status_code == 401


async def test_sin_cabecera_authorization_se_rechaza(client):
    response = client.get("/api/usuarios")

    assert response.status_code == 401


async def test_un_ticket_sse_no_sirve_como_token_de_acceso(client, token_admin):
    """Separación de audiencias: el ticket SSE se emite con `aud` propia y
    viaja por la URL. Si valiera como Bearer, un ticket filtrado en los logs de
    un proxy daría acceso a toda la API."""
    ticket = client.post("/api/auth/sse-ticket", headers=auth_header(token_admin)).json()["ticket"]

    response = client.get("/api/usuarios", headers=auth_header(ticket))

    assert response.status_code == 401


async def test_un_token_de_acceso_no_sirve_como_ticket_sse(client, token_admin):
    """La separación tiene que valer en los dos sentidos."""
    response = client.get("/api/sse/lecturas", params={"ticket": token_admin})

    assert response.status_code == 401
