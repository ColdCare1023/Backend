from tests.conftest import auth_header


def _login(client, email: str, password: str = "password123"):
    return client.post("/api/auth/login", data={"username": email, "password": password})


async def test_login_de_usuario_nuevo_exige_consentimiento(client, crear_usuario):
    from src.domain.value_objects.rol import Rol

    await crear_usuario("Nuevo", "nuevo.privacidad@farmacia.example.org", "password123", Rol.TECNICO)
    response = _login(client, "nuevo.privacidad@farmacia.example.org")

    assert response.status_code == 200
    assert response.json()["require_privacy_consent"] is True


async def test_aceptar_privacidad_marca_consentimiento_y_no_se_vuelve_a_pedir(client, crear_usuario):
    from src.domain.value_objects.rol import Rol

    await crear_usuario("Consciente", "consciente@farmacia.example.org", "password123", Rol.TECNICO)
    token = _login(client, "consciente@farmacia.example.org").json()["access_token"]

    aceptar = client.post("/api/auth/privacidad/aceptar", headers=auth_header(token))
    assert aceptar.status_code == 200
    body = aceptar.json()
    assert body["privacy_accepted"] is True
    assert body["privacy_version_accepted"] == "1.0"

    segundo_login = _login(client, "consciente@farmacia.example.org")
    assert segundo_login.json()["require_privacy_consent"] is False


async def test_rechazar_privacidad_revoca_token_y_bloquea_sesion(client, crear_usuario):
    from src.domain.value_objects.rol import Rol

    await crear_usuario("Rechazon", "rechazon@farmacia.example.org", "password123", Rol.TECNICO)
    token = _login(client, "rechazon@farmacia.example.org").json()["access_token"]

    rechazar = client.post("/api/auth/privacidad/rechazar", headers=auth_header(token))
    assert rechazar.status_code == 401

    # El token recién usado queda revocado: cualquier llamada posterior falla.
    intento_posterior = client.get("/api/usuarios", headers=auth_header(token))
    assert intento_posterior.status_code == 401


async def test_aceptacion_privacidad_queda_en_trazabilidad_encadenada(client, crear_usuario, token_tecnico):
    from src.domain.value_objects.rol import Rol

    await crear_usuario("Auditado", "auditado@farmacia.example.org", "password123", Rol.TECNICO)
    token = _login(client, "auditado@farmacia.example.org").json()["access_token"]
    client.post("/api/auth/privacidad/aceptar", headers=auth_header(token))

    trazabilidad = client.get(
        "/api/trazabilidad", params={"tipo_evento": "ACEPTACION_PRIVACIDAD"}, headers=auth_header(token_tecnico)
    )
    assert trazabilidad.status_code == 200
    assert len(trazabilidad.json()) == 1
    assert trazabilidad.json()[0]["payload"]["email"] == "auditado@farmacia.example.org"
