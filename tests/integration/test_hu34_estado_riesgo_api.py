"""HU-34 esc. 4 por la API: `estado_riesgo` distingue la lectura sin
clasificación del modelo y la falla del DS18B20 del Normal que deja la regla
2-8 °C en `riesgo_efectivo` (que se conserva igual por compatibilidad)."""

from datetime import datetime, timedelta, timezone

from tests.conftest import auth_header

DEVICE_ID = "FARM-HU34-01"
_BASE = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=30)


def _post(client, token, minuto: int, temperatura: float | None) -> dict:
    respuesta = client.post(
        "/api/lecturas",
        json={
            "device_id": DEVICE_ID,
            "timestamp": (_BASE + timedelta(minutes=minuto)).isoformat(),
            "temperatura_interna": temperatura,
            "temperatura_ambiental": 21.0,
            "humedad_ambiental": 55.0,
            "apertura_refrigerador": False,
            "estado_conectividad": "online",
        },
        headers=auth_header(token),
    )
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


def test_sin_ventana_historica_no_se_rotula_normal(client, token_tecnico):
    primera = _post(client, token_tecnico, 0, 5.0)

    assert primera["riesgo_efectivo"] == "normal"  # la regla 2-8 °C, sin cambios
    assert primera["estado_inferencia"] == "omitida"
    assert primera["estado_riesgo"] == "sin_clasificacion_ia"


def test_con_clasificacion_del_modelo_es_normal(client, token_tecnico):
    for minuto in (0, 1):
        _post(client, token_tecnico, minuto, 5.0)
    tercera = _post(client, token_tecnico, 2, 5.0)

    assert tercera["estado_inferencia"] == "completada"
    assert tercera["estado_riesgo"] == "normal"


def test_excursion_y_falla_de_sensor(client, token_tecnico):
    fuera = _post(client, token_tecnico, 0, 9.5)
    sin_ds18b20 = _post(client, token_tecnico, 1, None)

    assert fuera["estado_riesgo"] == "excursion_critica"
    assert sin_ds18b20["riesgo_efectivo"] is None
    assert sin_ds18b20["estado_riesgo"] == "falla_sensor"


def test_el_historial_y_la_evidencia_tambien_lo_exponen(client, token_tecnico, token_farmaceutico):
    lectura = _post(client, token_tecnico, 0, 5.0)

    historial = client.get(f"/api/lecturas?device_id={DEVICE_ID}", headers=auth_header(token_tecnico)).json()
    evidencia = client.get(f"/api/ia/inferencia/{lectura['id']}", headers=auth_header(token_farmaceutico)).json()

    assert historial[0]["estado_riesgo"] == "sin_clasificacion_ia"
    assert evidencia["estado_riesgo"] == "sin_clasificacion_ia"
