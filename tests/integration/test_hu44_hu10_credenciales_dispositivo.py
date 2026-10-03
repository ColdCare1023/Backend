"""HU-44/HU-10 (backlog de 51 HU finales): rotación y revocación de
credenciales MQTT por dispositivo, y el endpoint de autenticación que EMQX
Cloud puede usar como backend HTTP."""

from tests.conftest import auth_header

DEVICE_ID = "FARM-HU44-01"


def _provisionar(client, token_tecnico, device_id=DEVICE_ID) -> None:
    client.post(
        "/api/lecturas",
        json={
            "device_id": device_id,
            "timestamp": "2026-09-01T12:00:00Z",
            "temperatura_interna": 5.0,
            "temperatura_ambiental": 21.0,
            "humedad_ambiental": 55.0,
            "apertura_refrigerador": False,
            "estado_conectividad": "online",
        },
        headers=auth_header(token_tecnico),
    )


def test_rotar_credencial_devuelve_token_en_claro_una_vez(client, token_tecnico, token_admin):
    _provisionar(client, token_tecnico)

    respuesta = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar",
        json={"motivo": "aprovisionamiento inicial"},
        headers=auth_header(token_admin),
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["device_id"] == DEVICE_ID
    assert len(cuerpo["token"]) > 20


def test_token_recien_rotado_autentica_correctamente(client, token_tecnico, token_admin):
    _provisionar(client, token_tecnico)
    token = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar",
        json={"motivo": "aprovisionamiento inicial"},
        headers=auth_header(token_admin),
    ).json()["token"]

    respuesta = client.post(
        "/api/dispositivos/autenticar", json={"device_id": DEVICE_ID, "token": token}
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["autorizado"] is True


def test_token_incorrecto_no_autentica(client, token_tecnico, token_admin):
    _provisionar(client, token_tecnico)
    client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar",
        json={"motivo": "aprovisionamiento inicial"},
        headers=auth_header(token_admin),
    )

    respuesta = client.post(
        "/api/dispositivos/autenticar", json={"device_id": DEVICE_ID, "token": "token-incorrecto"}
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["autorizado"] is False


def test_dispositivo_sin_credencial_provisionada_no_autentica(client, token_tecnico):
    _provisionar(client, token_tecnico)

    respuesta = client.post(
        "/api/dispositivos/autenticar", json={"device_id": DEVICE_ID, "token": "cualquier-cosa"}
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["autorizado"] is False


def test_dispositivo_inexistente_no_autentica(client):
    respuesta = client.post(
        "/api/dispositivos/autenticar", json={"device_id": "NO-EXISTE", "token": "cualquier-cosa"}
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["autorizado"] is False


def test_revocar_invalida_el_token_de_inmediato(client, token_tecnico, token_admin):
    _provisionar(client, token_tecnico)
    token = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar",
        json={"motivo": "aprovisionamiento inicial"},
        headers=auth_header(token_admin),
    ).json()["token"]

    revocacion = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/revocar",
        json={"motivo": "token posiblemente comprometido"},
        headers=auth_header(token_admin),
    )
    assert revocacion.status_code == 204

    respuesta = client.post(
        "/api/dispositivos/autenticar", json={"device_id": DEVICE_ID, "token": token}
    )
    assert respuesta.json()["autorizado"] is False


def test_rotar_no_admin_es_rechazado(client, token_tecnico, token_farmaceutico):
    _provisionar(client, token_tecnico)

    respuesta = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar",
        json={"motivo": "intento no autorizado"},
        headers=auth_header(token_farmaceutico),
    )
    assert respuesta.status_code == 403


def test_rotar_dispositivo_inexistente_da_404(client, token_admin):
    respuesta = client.post(
        "/api/dispositivos/NO-EXISTE/credenciales/rotar",
        json={"motivo": "no existe"},
        headers=auth_header(token_admin),
    )
    assert respuesta.status_code == 404


def test_rotacion_y_revocacion_quedan_auditadas_y_encadenadas(client, token_tecnico, token_admin):
    _provisionar(client, token_tecnico)
    client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar",
        json={"motivo": "aprovisionamiento inicial"},
        headers=auth_header(token_admin),
    )
    client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/revocar",
        json={"motivo": "compromiso sospechado"},
        headers=auth_header(token_admin),
    )

    trazabilidad = client.get("/api/trazabilidad", headers=auth_header(token_admin)).json()
    tipos = [r["tipo_evento"] for r in trazabilidad]
    assert "ROTACION_CREDENCIAL_DISPOSITIVO" in tipos
    assert "REVOCACION_CREDENCIAL_DISPOSITIVO" in tipos

    auditoria = client.get("/api/auditoria", headers=auth_header(token_admin)).json()
    acciones = [entrada["accion"] for entrada in auditoria]
    assert "ROTAR_CREDENCIAL_DISPOSITIVO" in acciones
    assert "REVOCAR_CREDENCIAL_DISPOSITIVO" in acciones


def test_rotar_no_afecta_telemetria_historica(client, token_tecnico, token_admin):
    """HU-44 Escenario 2: la telemetría ya persistida permanece intacta."""
    _provisionar(client, token_tecnico)
    antes = client.get(
        "/api/lecturas", params={"device_id": DEVICE_ID}, headers=auth_header(token_tecnico)
    ).json()

    client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar",
        json={"motivo": "rotación de rutina"},
        headers=auth_header(token_admin),
    )

    despues = client.get(
        "/api/lecturas", params={"device_id": DEVICE_ID}, headers=auth_header(token_tecnico)
    ).json()
    assert antes == despues
