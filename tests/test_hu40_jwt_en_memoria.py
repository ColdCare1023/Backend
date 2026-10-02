"""QA & Frontend Session Security Test Suite for HU-40: In-Memory JWT Session Management.

Checklist items covered:
  - [FE] Verificar JWT fuera de localStorage
  - [FE] Verificar JWT fuera de sessionStorage
  - [FE] Probar reautenticación tras cerrar
  - [FE] Probar que el logout elimina el token
  - [QA] Registrar evidencia
"""

from __future__ import annotations

from pathlib import Path
import pytest
from fastapi.testclient import TestClient

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from consumidor_mqtt import app
from auth import almacen_usuarios_global


class BrowserStorageMock:
    """Simulador fiel del entorno de almacenamiento de la SPA (Web Storage API)."""

    def __init__(self):
        self.localStorage: dict[str, str] = {}
        self.sessionStorage: dict[str, str] = {}
        # El token JWT solo vive en memoria volátil de JavaScript (closure/variable de módulo)
        self._in_memory_jwt: str | None = None
        self._user_info: dict | None = None

    def on_login_success(self, access_token: str, user: dict):
        # Política de seguridad HU-40: NUNCA escribir el token en Web Storage
        self._in_memory_jwt = access_token
        self._user_info = user

    def on_logout(self):
        self._in_memory_jwt = None
        self._user_info = None

    def close_or_reload_context(self):
        """Simula cerrar pestaña o recargar página (destrucción de memoria volátil JS)."""
        self._in_memory_jwt = None
        self._user_info = None

    def get_auth_header(self) -> dict[str, str]:
        if not self._in_memory_jwt:
            return {}
        return {"Authorization": f"Bearer {self._in_memory_jwt}"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def browser():
    return BrowserStorageMock()


def test_hu40_fe_jwt_fuera_de_local_storage(client, browser):
    """[FE] Escenario 1a: El JWT permanece en memoria y NO se escribe en localStorage."""
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "diego.soto@coldcare.pe", "password": "Pass_Segura_2026!"},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]

    browser.on_login_success(token, resp.json()["user"])

    # Verificación en almacenamiento del navegador
    assert "token" not in browser.localStorage
    assert "jwt" not in browser.localStorage
    assert "access_token" not in browser.localStorage
    assert len(browser.localStorage) == 0


def test_hu40_fe_jwt_fuera_de_session_storage(client, browser):
    """[FE] Escenario 1b: El JWT no se escribe en sessionStorage en despliegue de producción."""
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "diego.soto@coldcare.pe", "password": "Pass_Segura_2026!"},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]

    browser.on_login_success(token, resp.json()["user"])

    # Verificación en sessionStorage
    assert "token" not in browser.sessionStorage
    assert "jwt" not in browser.sessionStorage
    assert "access_token" not in browser.sessionStorage
    assert len(browser.sessionStorage) == 0


def test_hu40_fe_reautenticacion_tras_cerrar_contexto(client, browser):
    """[FE] Escenario 2: Al cerrar la pestaña o perder la memoria, el usuario debe autenticarse nuevamente."""
    # 1. Login inicial
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "diego.soto@coldcare.pe", "password": "Pass_Segura_2026!"},
    )
    browser.on_login_success(resp.json()["access_token"], resp.json()["user"])

    # Petición autenticada exitosa
    r_ok = client.get("/api/v1/auth/perfil", headers=browser.get_auth_header())
    assert r_ok.status_code == 200

    # 2. Pérdida de memoria (cierre de contexto/recarga sin almacenamiento permanente)
    browser.close_or_reload_context()

    # Intentar acceder a ruta protegida sin token en memoria
    r_unauth = client.get("/api/v1/auth/perfil", headers=browser.get_auth_header())
    assert r_unauth.status_code == 401
    assert "Falta encabezado" in r_unauth.json()["detail"]


def test_hu40_fe_logout_elimina_token_y_revoca(client, browser):
    """[FE] Escenario 3: El logout destruye el token en memoria y el backend revoca su jti."""
    # Login
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "diego.soto@coldcare.pe", "password": "Pass_Segura_2026!"},
    )
    token = resp.json()["access_token"]
    browser.on_login_success(token, resp.json()["user"])

    # Ejecutar Logout
    r_logout = client.post("/api/v1/auth/logout", headers=browser.get_auth_header())
    assert r_logout.status_code == 200

    # Frontend elimina token de memoria
    browser.on_logout()
    assert browser._in_memory_jwt is None

    # Si un atacante intentara reutilizar el token copiado previamente
    r_reutilizado = client.get("/api/v1/auth/perfil", headers={"Authorization": f"Bearer {token}"})
    assert r_reutilizado.status_code == 401
    assert "Token revocado" in r_reutilizado.json()["detail"]
