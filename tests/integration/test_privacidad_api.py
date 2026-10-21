from tests.conftest import auth_header


def _login(client, email: str, password: str = "password123"):
    return client.post("/api/auth/login", data={"username": email, "password": password})


async def test_login_de_usuario_nuevo_exige_consentimiento(client, crear_usuario):
    from src.domain.value_objects.rol import Rol

    await crear_usuario("Nuevo", "nuevo.privacidad@farmacia.example.org", "password123", Rol.TECNICO, con_consentimiento=False)
    response = _login(client, "nuevo.privacidad@farmacia.example.org")

    assert response.status_code == 200
    assert response.json()["require_privacy_consent"] is True


async def test_aceptar_privacidad_marca_consentimiento_y_no_se_vuelve_a_pedir(client, crear_usuario):
    from src.domain.value_objects.rol import Rol

    await crear_usuario("Consciente", "consciente@farmacia.example.org", "password123", Rol.TECNICO, con_consentimiento=False)
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

    await crear_usuario("Rechazon", "rechazon@farmacia.example.org", "password123", Rol.TECNICO, con_consentimiento=False)
    token = _login(client, "rechazon@farmacia.example.org").json()["access_token"]

    rechazar = client.post("/api/auth/privacidad/rechazar", headers=auth_header(token))
    assert rechazar.status_code == 401

    # El token recién usado queda revocado: cualquier llamada posterior falla.
    intento_posterior = client.get("/api/usuarios", headers=auth_header(token))
    assert intento_posterior.status_code == 401


async def test_aceptacion_privacidad_queda_en_trazabilidad_encadenada(
    client, crear_usuario, token_tecnico, token_auditor
):
    from src.domain.value_objects.rol import Rol

    await crear_usuario("Auditado", "auditado@farmacia.example.org", "password123", Rol.TECNICO, con_consentimiento=False)
    token = _login(client, "auditado@farmacia.example.org").json()["access_token"]
    client.post("/api/auth/privacidad/aceptar", headers=auth_header(token))

    trazabilidad = client.get(
        "/api/trazabilidad", params={"tipo_evento": "ACEPTACION_PRIVACIDAD"}, headers=auth_header(token_tecnico)
    )
    assert trazabilidad.status_code == 200
    assert len(trazabilidad.json()) == 1
    # BE-014: el eslabón identifica al titular por usuario_id, sin su correo.
    # BE-005: la IP de origen solo la ve el rol de auditoría.
    payload = trazabilidad.json()[0]["payload"]
    assert "email" not in payload
    assert payload["ip_origen"] == "[redactado]"
    completa = client.get(
        "/api/trazabilidad", params={"tipo_evento": "ACEPTACION_PRIVACIDAD"}, headers=auth_header(token_auditor)
    )
    assert completa.json()[0]["payload"]["usuario_id"] == payload["usuario_id"]
    assert completa.json()[0]["payload"]["ip_origen"] is not None


async def test_sin_consentimiento_no_se_accede_a_recursos_protegidos(client, crear_usuario):
    """BE-014: el consentimiento se exige de verdad, no se presume."""
    from src.domain.value_objects.rol import Rol

    await crear_usuario("Sin", "sin.consentimiento@farmacia.example.org", "password123", Rol.TECNICO,
                        con_consentimiento=False)
    token = _login(client, "sin.consentimiento@farmacia.example.org").json()["access_token"]

    assert client.get("/api/lecturas", headers=auth_header(token)).status_code == 403
    assert client.post("/api/auth/privacidad/aceptar", headers=auth_header(token)).status_code == 200
    assert client.get("/api/lecturas", headers=auth_header(token)).status_code == 200


async def test_usuario_creado_por_el_administrador_debe_consentir(client, token_admin):
    from tests.conftest import auth_header as cabecera

    client.post(
        "/api/usuarios",
        json={"nombre": "Alta", "email": "alta.nueva@farmacia.example.org", "password": "ClaveSegura123",
              "rol": "tecnico"},
        headers=cabecera(token_admin),
    )
    respuesta = _login(client, "alta.nueva@farmacia.example.org", "ClaveSegura123")
    assert respuesta.json()["require_privacy_consent"] is True
