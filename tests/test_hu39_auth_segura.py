"""QA & Backend Validation Test Suite for HU-39: Secure User Authentication (Argon2id + JWT + Rate Limiting).

Checklist items covered:
  - [BE] Verificar login con credenciales válidas
  - [BE] Probar rechazo con credenciales inválidas
  - [BE] Probar bloqueo por intentos fallidos
  - [FE] Verificar expiración de sesión
  - [QA] Registrar evidencia
"""

from __future__ import annotations

import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from consumidor_mqtt import app
from auth import almacen_usuarios_global, crear_access_token


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_hu39_be_login_con_credenciales_validas(client):
    """[BE] Escenario 1: Autenticación exitosa genera JWT con identidad y rol."""
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "diego.soto@coldcare.pe", "password": "Pass_Segura_2026!"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 900
    assert data["user"]["email"] == "diego.soto@coldcare.pe"
    assert data["user"]["role"] == "administrador"

    # Verificar acceso con el token generado al endpoint protegido
    token = data["access_token"]
    resp_perfil = client.get("/api/v1/auth/perfil", headers={"Authorization": f"Bearer {token}"})
    assert resp_perfil.status_code == 200
    assert resp_perfil.json()["email"] == "diego.soto@coldcare.pe"


def test_hu39_be_rechazo_con_credenciales_invalidas(client):
    """[BE] Escenario 2: Correo inexistente o contraseña errónea se rechazan con mensaje genérico."""
    # 1. Contraseña incorrecta
    resp1 = client.post(
        "/api/v1/auth/login",
        json={"email": "tecnico@coldcare.pe", "password": "PasswordIncorrecta!"},
    )
    assert resp1.status_code == 401
    assert resp1.json()["detail"] == "Credenciales inválidas"

    # 2. Correo inexistente (retorna exactamente el mismo mensaje para evitar enumeración)
    resp2 = client.post(
        "/api/v1/auth/login",
        json={"email": "no_existe@coldcare.pe", "password": "PasswordCualquiera!"},
    )
    assert resp2.status_code == 401
    assert resp2.json()["detail"] == "Credenciales inválidas"

    # Verificar que los intentos quedaron en el log de auditoría
    audit = almacen_usuarios_global.audit_log
    assert any(a["resultado"] == "rechazado" and a["email"] == "tecnico@coldcare.pe" for a in audit)
    assert any(a["resultado"] == "rechazado" and a["email"] == "no_existe@coldcare.pe" for a in audit)


def test_hu39_be_bloqueo_por_intentos_fallidos(client):
    """[BE] Escenario 3: Bloqueo de cuenta tras superar el umbral de 5 intentos fallidos consecutivos."""
    email = "tecnico@coldcare.pe"
    almacen_usuarios_global.registrar(email, "Tecnico_12345!", role="tecnico")

    # Ejecutar 5 intentos con clave errónea
    for i in range(1, 5):
        r = client.post("/api/v1/auth/login", json={"email": email, "password": f"Bad_{i}"})
        assert r.status_code == 401

    # El 5to intento activa el bloqueo por rate limiting (429)
    r5 = client.post("/api/v1/auth/login", json={"email": email, "password": "Bad_5"})
    assert r5.status_code == 429
    assert "bloqueada" in r5.json()["detail"]

    # Incluso con la contraseña correcta ahora es rechazado por estar bloqueado
    r_locked = client.post("/api/v1/auth/login", json={"email": email, "password": "Tecnico_12345!"})
    assert r_locked.status_code == 429

    # Limpiar estado para otros tests
    user = almacen_usuarios_global.usuarios[email]
    user.locked_until = None
    user.failed_attempts = 0


def test_hu39_be_usuario_desactivado_no_autentica(client):
    """[BE] Escenario 4: Un usuario desactivado no obtiene sesión válida."""
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "inactivo@coldcare.pe", "password": "Inactivo_12345!"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Credenciales inválidas"


def test_hu39_fe_verificar_expiracion_de_sesion(client):
    """[FE/BE] Token expirado retorna 401 permitiendo a la SPA detectar expiración y exigir reautenticación."""
    # Crear un token ya expirado (exp_seconds = -10)
    token_expirado = crear_access_token("diego.soto@coldcare.pe", "administrador", exp_seconds=-10)

    resp = client.get("/api/v1/auth/perfil", headers={"Authorization": f"Bearer {token_expirado}"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Token expirado"
