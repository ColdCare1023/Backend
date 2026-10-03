"""B8.4: barrido automático de autenticación sobre TODOS los endpoints.

Las pruebas por endpoint cubren los que alguien se acordó de escribir. Esta
recorre el esquema OpenAPI en tiempo de ejecución, así que un endpoint nuevo
que se despliegue sin protección falla aquí sin que nadie tenga que añadir un
caso — que es justo el error que nadie detecta a mano."""

import pytest

from src.interface.main import create_app
from tests.conftest import auth_header

# Endpoints deliberadamente públicos, con el motivo por el que lo son.
PUBLICOS = {
    ("GET", "/health"): "probe de la plataforma; no expone datos del negocio",
    ("POST", "/api/auth/login"): "punto de entrada de la autenticación",
    # Público por la misma razón que /login: es el punto de entrada. La
    # autorización no desaparece, se desplaza — el ID token de Google se
    # verifica contra el JWKS de Google y el correo debe existir y estar activo
    # en `users`. Cubierto por tests/integration/test_auth_google_api.py.
    ("POST", "/api/auth/google"): "punto de entrada de la autenticación con Google",
    ("GET", "/openapi.json"): "solo se sirve fuera de producción",
    ("GET", "/docs"): "solo se sirve fuera de producción",
    ("GET", "/docs/oauth2-redirect"): "solo se sirve fuera de producción",
    ("GET", "/redoc"): "solo se sirve fuera de producción",
    # El stream SSE no puede llevar cabecera Authorization (EventSource no la
    # envía): se autentica con un ticket de un solo uso emitido por
    # POST /api/auth/sse-ticket, que sí exige JWT.
    ("GET", "/api/sse/lecturas"): "autenticado por ticket efímero, no por cabecera",
    # HU-44/HU-10: backend de autenticación HTTP para EMQX Cloud — lo llama
    # el broker (con el device_id + token del ESP32 en el cuerpo), no un
    # usuario con sesión. La autorización se verifica dentro del propio caso
    # de uso (hash del token por dispositivo), no vía JWT.
    ("POST", "/api/dispositivos/autenticar"): "backend de autenticación del broker MQTT, no un usuario",
}

# Valores de ejemplo para los parámetros de ruta. El contenido es irrelevante:
# la autorización debe rechazarse ANTES de mirar si el recurso existe.
_EJEMPLOS = {
    "lectura_id": "00000000-0000-0000-0000-000000000000",
    "alerta_id": "00000000-0000-0000-0000-000000000000",
    "usuario_id": "00000000-0000-0000-0000-000000000000",
    "registro_id": "00000000-0000-0000-0000-000000000000",
    "despliegue_id": "00000000-0000-0000-0000-000000000000",
    "device_id": "ESP32-RBAC-01",
}


def _endpoints() -> list[tuple[str, str]]:
    esquema = create_app().openapi()
    return sorted(
        (metodo.upper(), ruta)
        for ruta, operaciones in esquema["paths"].items()
        for metodo in operaciones
        if metodo.upper() not in {"HEAD", "OPTIONS"}
    )


def _concretar(ruta: str) -> str:
    for nombre, valor in _EJEMPLOS.items():
        ruta = ruta.replace("{" + nombre + "}", valor)
    return ruta


ENDPOINTS_PROTEGIDOS = [
    (metodo, ruta) for metodo, ruta in _endpoints() if (metodo, ruta) not in PUBLICOS
]


def test_el_barrido_encuentra_endpoints():
    """Red de seguridad de la propia prueba: si la enumeración se rompiera,
    el barrido pasaría en verde sin comprobar nada."""
    assert len(ENDPOINTS_PROTEGIDOS) >= 25


@pytest.mark.parametrize("metodo,ruta", ENDPOINTS_PROTEGIDOS)
def test_todo_endpoint_exige_autenticacion(client, metodo, ruta):
    respuesta = client.request(metodo, _concretar(ruta), json={})
    assert respuesta.status_code == 401, (
        f"{metodo} {ruta} respondió {respuesta.status_code} sin token; "
        "si es público a propósito, decláralo en PUBLICOS con su motivo"
    )


@pytest.mark.parametrize("metodo,ruta", ENDPOINTS_PROTEGIDOS)
def test_token_invalido_se_rechaza(client, metodo, ruta):
    respuesta = client.request(
        metodo, _concretar(ruta), json={}, headers=auth_header("token.falsificado.xyz")
    )
    assert respuesta.status_code == 401, f"{metodo} {ruta} aceptó un token inválido"


# ── Segregación de funciones por rol ──────────────────────────────────────
# Cada fila declara qué rol NO debe poder tocar un endpoint sensible. Son los
# límites que la tesis afirma sostener (RNF-05, mínimo privilegio).
@pytest.mark.parametrize(
    "metodo,ruta,rol_prohibido",
    [
        ("GET", "/api/usuarios", "tecnico"),
        ("POST", "/api/usuarios", "tecnico"),
        ("GET", "/api/usuarios", "farmaceutico"),
        ("POST", "/api/usuarios", "farmaceutico"),
        ("GET", "/api/auditoria", "tecnico"),
        ("GET", "/api/auditoria", "farmaceutico"),
        ("GET", "/api/dispositivos", "tecnico"),
        ("POST", "/api/dispositivos/ESP32-RBAC-01/baja", "tecnico"),
        ("POST", "/api/firmware/releases", "tecnico"),
        ("POST", "/api/checklist-bpa", "tecnico"),
        ("PATCH", "/api/dispositivos/ESP32-RBAC-01/calibracion", "tecnico"),
        ("GET", "/api/reportes/bpa/pdf", "tecnico"),
    ],
)
def test_rol_sin_permiso_recibe_403(
    client, metodo, ruta, rol_prohibido, token_tecnico, token_farmaceutico
):
    token = {"tecnico": token_tecnico, "farmaceutico": token_farmaceutico}[rol_prohibido]
    respuesta = client.request(metodo, ruta, json={}, headers=auth_header(token))
    assert respuesta.status_code == 403, (
        f"{metodo} {ruta} devolvió {respuesta.status_code} al rol '{rol_prohibido}', "
        "que no debería tener acceso"
    )
