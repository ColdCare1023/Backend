"""Controles de seguridad transversales: headers, rate limiting, SSE y política de contraseñas."""

from datetime import datetime, timezone

import jwt as pyjwt
import pytest

from src.domain.value_objects.rol import Rol
from src.infrastructure.config import Settings
from src.infrastructure.security.jwt_handler import JWTHandler
from tests.conftest import auth_header

# ── Security headers ─────────────────────────────────────────────


def test_respuestas_incluyen_headers_de_seguridad(client):
    response = client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "Permissions-Policy" in response.headers
    assert response.headers["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'"


def test_docs_no_llevan_csp_restrictiva_para_poder_renderizar(client):
    response = client.get("/docs")
    assert response.status_code == 200
    assert "Content-Security-Policy" not in response.headers


def test_endpoints_de_auth_no_son_cacheables(client, crear_usuario):
    response = client.post(
        "/api/auth/login", data={"username": "nadie@farmacia.example.org", "password": "loquesea123"}
    )
    assert response.headers["Cache-Control"] == "no-store"


async def test_los_datos_personales_de_la_api_tampoco_son_cacheables(client, token_admin):
    """Ley N.° 29733: el historial, la auditoría y los reportes contienen datos
    personales y sanitarios. `no-store` solo cubría /api/auth, así que el resto
    podía quedar retenido en cachés intermedias fuera del control del titular."""
    for ruta in ("/api/auditoria", "/api/usuarios"):
        response = client.get(ruta, headers=auth_header(token_admin))
        assert response.status_code == 200, ruta
        assert response.headers["Cache-Control"] == "no-store", ruta


# ── Rate limiting de login ───────────────────────────────────────


async def test_login_se_bloquea_tras_cinco_intentos_fallidos(client, crear_usuario):
    await crear_usuario("Ana", "ana@farmacia.example.org", "correcta123", Rol.TECNICO)
    for _ in range(5):
        response = client.post(
            "/api/auth/login",
            data={"username": "ana@farmacia.example.org", "password": "incorrecta"},
        )
        assert response.status_code == 401

    response = client.post(
        "/api/auth/login",
        data={"username": "ana@farmacia.example.org", "password": "correcta123"},
    )
    assert response.status_code == 429
    assert "Retry-After" in response.headers


async def test_login_exitoso_reinicia_el_contador_de_fallos(client, crear_usuario):
    await crear_usuario("Beto", "beto@farmacia.example.org", "correcta123", Rol.TECNICO)
    for _ in range(4):
        client.post(
            "/api/auth/login",
            data={"username": "beto@farmacia.example.org", "password": "incorrecta"},
        )
    ok = client.post(
        "/api/auth/login",
        data={"username": "beto@farmacia.example.org", "password": "correcta123"},
    )
    assert ok.status_code == 200

    # Tras el éxito la ventana queda limpia: un fallo aislado no bloquea.
    response = client.post(
        "/api/auth/login",
        data={"username": "beto@farmacia.example.org", "password": "incorrecta"},
    )
    assert response.status_code == 401


def test_login_con_email_inexistente_no_revela_si_existe(client):
    response = client.post(
        "/api/auth/login",
        data={"username": "fantasma@farmacia.example.org", "password": "loquesea123"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Email o contraseña incorrectos"


async def test_intentos_de_login_quedan_auditados(client, crear_usuario, token_admin):
    client.post(
        "/api/auth/login",
        data={"username": "intruso@farmacia.example.org", "password": "invalida123"},
    )
    response = client.get("/api/auditoria", headers=auth_header(token_admin))
    assert response.status_code == 200
    acciones = [registro["accion"] for registro in response.json()]
    assert "LOGIN_FALLIDO" in acciones
    assert "LOGIN_EXITOSO" in acciones  # el del propio token_admin


# ── JWT hardening ────────────────────────────────────────────────


def test_token_incluye_claims_iss_aud_iat_jti(token_admin):
    claims = pyjwt.decode(token_admin, options={"verify_signature": False})
    assert claims["iss"] == "cadena-frio-backend"
    assert claims["aud"] == "cadena-frio-api"
    assert "iat" in claims
    assert len(claims["jti"]) == 32


# ── SSE autenticado con ticket efímero ───────────────────────────


def test_sse_sin_ticket_es_rechazado(client):
    response = client.get("/api/sse/lecturas")
    assert response.status_code == 422


def test_sse_con_ticket_invalido_es_rechazado(client):
    response = client.get("/api/sse/lecturas", params={"ticket": "basura"})
    assert response.status_code == 401


def test_access_token_no_sirve_como_ticket_sse(client, token_admin):
    # Audiencias distintas: un JWT de sesión no abre el stream.
    response = client.get("/api/sse/lecturas", params={"ticket": token_admin})
    assert response.status_code == 401


def test_ticket_emitido_por_el_endpoint_es_valido_para_sse(client, token_admin):
    # Nota: no se abre el stream con TestClient porque el generador SSE es
    # infinito y el cliente de pruebas nunca reporta la desconexión (colgaría
    # la suite). El roundtrip criptográfico del ticket se valida directamente.
    ticket = client.post("/api/auth/sse-ticket", headers=auth_header(token_admin)).json()["ticket"]
    handler = JWTHandler(Settings())
    assert handler.validar_ticket_sse(ticket)


def test_emitir_ticket_sse_requiere_sesion(client):
    response = client.post("/api/auth/sse-ticket")
    assert response.status_code == 401


# ── Política de contraseñas ──────────────────────────────────────


@pytest.mark.parametrize("password", ["corta1", "sololetras", "0123456789"])
def test_crear_usuario_rechaza_passwords_debiles(client, token_admin, password):
    response = client.post(
        "/api/usuarios",
        json={
            "nombre": "Nuevo",
            "email": "nuevo@farmacia.example.org",
            "password": password,
            "rol": "tecnico",
        },
        headers=auth_header(token_admin),
    )
    assert response.status_code == 422


# ── Configuración de producción ──────────────────────────────────


def test_settings_rechaza_secreto_por_defecto_en_produccion():
    # Se pasa el secreto por defecto explícitamente: el conftest ya define
    # JWT_SECRET_KEY en el entorno y taparía el valor por defecto real.
    with pytest.raises(ValueError):
        Settings(
            environment="production",
            jwt_secret_key="clave_secreta_larga_y_aleatoria_cambiar_en_produccion",
        )


def test_settings_rechaza_secreto_corto_en_produccion():
    with pytest.raises(ValueError):
        Settings(environment="production", jwt_secret_key="corto")


def test_settings_rechaza_cors_comodin_en_produccion():
    with pytest.raises(ValueError):
        Settings(
            environment="production",
            jwt_secret_key="x" * 40,
            cors_origins=["*"],
        )


def test_docs_desactivados_en_produccion(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "s" * 40)
    monkeypatch.setenv("ALLOWED_HOSTS", '["api.farmacia.example.org"]')
    monkeypatch.setenv("CORS_ORIGINS", '["https://app.farmacia.example.org"]')
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://app:seguro@db.example.org:5432/farmacia")
    monkeypatch.setenv("MQTT_ENABLED", "false")
    from src.infrastructure.config import get_settings
    from src.interface.main import create_app

    get_settings.cache_clear()
    try:
        app = create_app()
        rutas = {getattr(ruta, "path", None) for ruta in app.routes}
        assert "/docs" not in rutas
        assert "/openapi.json" not in rutas
    finally:
        get_settings.cache_clear()


# ── Revocación de tokens (logout) ────────────────────────────────


async def test_logout_revoca_el_token(client, token_farmaceutico):
    ok = client.get("/api/alertas", headers=auth_header(token_farmaceutico))
    assert ok.status_code == 200

    logout = client.post("/api/auth/logout", headers=auth_header(token_farmaceutico))
    assert logout.status_code == 204

    despues = client.get("/api/alertas", headers=auth_header(token_farmaceutico))
    assert despues.status_code == 401
    assert "revocado" in despues.json()["detail"].lower()


async def test_logout_queda_en_auditoria(client, token_admin):
    client.post("/api/auth/logout", headers=auth_header(token_admin))

    # El admin necesita un token nuevo (el anterior quedó revocado).
    login = client.post(
        "/api/auth/login",
        data={"username": "admin@farmacia.example.org", "password": "password123"},
    )
    registros = client.get(
        "/api/auditoria", headers=auth_header(login.json()["access_token"])
    ).json()
    assert any(r["accion"] == "LOGOUT" for r in registros)


# ── Registro estricto de dispositivos (mínimo privilegio) ────────


async def test_dispositivo_no_registrado_se_rechaza_en_modo_estricto(
    app, client, token_tecnico, db_session_factory
):
    from src.infrastructure.config import get_settings

    settings_estrictos = get_settings().model_copy(
        update={"device_registry_estricto": True}
    )
    app.dependency_overrides[get_settings] = lambda: settings_estrictos

    lectura = {
        "device_id": "INTRUSO-99",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "temperatura_interna": 5.0,
        "temperatura_ambiental": 21.0,
        "humedad_ambiental": 55.0,
    }
    rechazo = client.post("/api/lecturas", json=lectura, headers=auth_header(token_tecnico))
    assert rechazo.status_code == 403

    # Un dispositivo provisionado sí puede registrar lecturas.
    from src.infrastructure.database.repositories.device_repository import (
        SQLAlchemyDeviceRepository,
    )

    async with db_session_factory() as session:
        await SQLAlchemyDeviceRepository(session).obtener_o_crear("FARM-01-CDL")
        await session.commit()

    lectura["device_id"] = "FARM-01-CDL"
    aceptado = client.post("/api/lecturas", json=lectura, headers=auth_header(token_tecnico))
    assert aceptado.status_code == 201


# ── Protección global de la API ──────────────────────────────────


def test_rate_limit_global_devuelve_429(monkeypatch):
    monkeypatch.setenv("API_RATE_LIMIT_MAX_SOLICITUDES", "3")
    from fastapi.testclient import TestClient

    from src.infrastructure.config import get_settings
    from src.interface.main import create_app

    get_settings.cache_clear()
    try:
        app = create_app()
        with TestClient(app) as cliente:
            for _ in range(3):
                assert cliente.get("/openapi.json").status_code == 200
            bloqueado = cliente.get("/openapi.json")
            assert bloqueado.status_code == 429
            assert "Retry-After" in bloqueado.headers
            # /health queda exento para los probes de la plataforma.
            assert cliente.get("/health").status_code == 200
    finally:
        get_settings.cache_clear()


def test_cuerpo_demasiado_grande_devuelve_413(client):
    respuesta = client.post(
        "/api/auth/login",
        data={"username": "a@b.c", "password": "x" * (70 * 1024)},
    )
    assert respuesta.status_code == 413


def test_content_length_malformado_no_provoca_error_500(client):
    respuesta = client.get("/api/alertas", headers={"Content-Length": "no-es-un-numero"})
    assert respuesta.status_code == 400


def test_settings_produccion_exige_hosts_y_tls_mqtt():
    with pytest.raises(ValueError):
        Settings(environment="production", jwt_secret_key="x" * 40, allowed_hosts=[])
    with pytest.raises(ValueError):
        Settings(
            environment="production",
            jwt_secret_key="x" * 40,
            allowed_hosts=["api.example.org"],
            mqtt_enabled=True,
            mqtt_tls_enabled=False,
        )
    with pytest.raises(ValueError):
        Settings(
            environment="production",
            jwt_secret_key="x" * 40,
            allowed_hosts=["api.example.org"],
            mqtt_enabled=True,
            mqtt_password="token_seguro",
        )


def test_settings_rechaza_entorno_ambiguo_y_placeholders_en_produccion():
    with pytest.raises(ValueError):
        Settings(environment="prod")
    with pytest.raises(ValueError):
        Settings(
            environment="production",
            jwt_secret_key="x" * 40,
            allowed_hosts=["api.example.org"],
            cors_origins=["https://app.example.org"],
            mqtt_enabled=False,
            database_url="postgresql+asyncpg://user:pass@localhost:5432/farmacia_db",
        )
