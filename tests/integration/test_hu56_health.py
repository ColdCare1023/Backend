"""HU-56: /health (liveness para Railway) y /health/detalle (operador)."""

from tests.conftest import auth_header


def test_health_responde_sin_autenticacion(client):
    respuesta = client.get("/health")
    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "ok"


def test_health_detalle_exige_administrador(client, token_tecnico):
    assert client.get("/health/detalle").status_code == 401
    assert client.get("/health/detalle", headers=auth_header(token_tecnico)).status_code == 403


def test_health_detalle_informa_bd_y_mqtt(client, token_admin):
    respuesta = client.get("/health/detalle", headers=auth_header(token_admin))
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["db"] == "ok"
    assert cuerpo["status"] == "ok"
    # En pruebas la ingesta MQTT no arranca (ENVIRONMENT=test).
    assert cuerpo["mqtt"]["habilitado"] is False
    assert cuerpo["mqtt"]["conectado"] is False
