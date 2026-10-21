"""BE-008: la adivinación de contraseñas se limita también por cuenta, no
solo por IP (atacante distribuido o proxy que oculta la IP real)."""

from src.domain.value_objects.rol import Rol
from src.infrastructure.security.rate_limiter import SlidingWindowRateLimiter

EMAIL = "objetivo@farmacia.example.org"


def _sin_limite_por_ip(app) -> None:
    """Simula IPs distintas: el límite por IP no actúa."""
    app.state.login_rate_limiter = SlidingWindowRateLimiter(max_intentos=10_000, ventana_segundos=300)
    app.state.login_cuenta_rate_limiter = SlidingWindowRateLimiter(max_intentos=3, ventana_segundos=900)


async def test_cuenta_se_bloquea_tras_fallos_aunque_cambie_la_ip(app, client, crear_usuario):
    await crear_usuario("Objetivo", EMAIL, "password123", Rol.TECNICO)
    _sin_limite_por_ip(app)

    for _ in range(3):
        assert client.post("/api/auth/login", data={"username": EMAIL, "password": "mala"}).status_code == 401
    # Ni siquiera la contraseña correcta entra mientras dure la ventana.
    bloqueado = client.post("/api/auth/login", data={"username": EMAIL.upper(), "password": "password123"})
    assert bloqueado.status_code == 429
    assert "Retry-After" in bloqueado.headers


async def test_otra_cuenta_no_se_ve_afectada(app, client, crear_usuario):
    await crear_usuario("Objetivo", EMAIL, "password123", Rol.TECNICO)
    await crear_usuario("Otra", "otra@farmacia.example.org", "password123", Rol.TECNICO)
    _sin_limite_por_ip(app)

    for _ in range(3):
        client.post("/api/auth/login", data={"username": EMAIL, "password": "mala"})
    ok = client.post("/api/auth/login", data={"username": "otra@farmacia.example.org", "password": "password123"})
    assert ok.status_code == 200


async def test_login_correcto_reinicia_el_contador_de_la_cuenta(app, client, crear_usuario):
    await crear_usuario("Objetivo", EMAIL, "password123", Rol.TECNICO)
    _sin_limite_por_ip(app)

    for _ in range(2):
        client.post("/api/auth/login", data={"username": EMAIL, "password": "mala"})
    assert client.post("/api/auth/login", data={"username": EMAIL, "password": "password123"}).status_code == 200
    for _ in range(2):
        client.post("/api/auth/login", data={"username": EMAIL, "password": "mala"})
    assert client.post("/api/auth/login", data={"username": EMAIL, "password": "password123"}).status_code == 200
