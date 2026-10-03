"""Prueba del hallazgo AI-06/AI-07 (auditoría de IA): la versión del modelo y
la confianza deben persistirse por lectura y llegar en la respuesta de la API
(el mismo contrato que consume el frontend vía SSE)."""

from datetime import datetime, timedelta, timezone

from tests.conftest import auth_header


def _payload(**overrides) -> dict:
    base = {
        "device_id": "FARM-AI-01",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "temperatura_ambiental": 6.0,
        "humedad_ambiental": 55.0,
        "temperatura_interna": 5.0,
        "apertura_refrigerador": False,
        "estado_conectividad": "online",
    }
    base.update(overrides)
    return base


async def test_lectura_persiste_confianza_y_version_del_modelo(client, token_tecnico):
    # HU-17: la IA necesita 2+ lecturas previas válidas para calcular
    # tendencia térmica antes de poder clasificar; se siembran dos.
    for minutos in (10, 5):
        client.post(
            "/api/lecturas",
            json=_payload(
                timestamp=(datetime.now(tz=timezone.utc) - timedelta(minutes=minutos)).isoformat()
            ),
            headers=auth_header(token_tecnico),
        )

    response = client.post("/api/lecturas", json=_payload(), headers=auth_header(token_tecnico))

    assert response.status_code == 201
    body = response.json()
    assert body["nivel_riesgo"] == "normal"
    assert body["confianza_ia"] is not None
    assert 0.0 <= body["confianza_ia"] <= 1.0
    assert body["modelo_version"] == "4.0.0-binario"
    assert body["origen_clasificacion"] == "random_forest"
    assert body["estado_inferencia"] == "completada"
    assert body["motivo_no_inferencia"] is None


async def test_lectura_sin_temperatura_interna_no_tiene_confianza_ni_alerta(client, token_tecnico):
    response = client.post(
        "/api/lecturas",
        json=_payload(
            timestamp=(datetime.now(tz=timezone.utc) - timedelta(minutes=5)).isoformat(),
            temperatura_interna=None,
        ),
        headers=auth_header(token_tecnico),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["nivel_riesgo"] is None
    # AIV-07: sin inferencia real, confianza_ia es NULL, nunca 0.0 como
    # valor centinela (0.0 sería indistinguible de "el modelo decidió con
    # confianza matemática cero").
    assert body["confianza_ia"] is None
    assert body["origen_clasificacion"] == "fallo_sensor"
    assert body["estado_inferencia"] == "omitida"
    assert body["motivo_no_inferencia"] == "sensor_interno_ausente"
