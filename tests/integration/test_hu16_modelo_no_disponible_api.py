"""HU-16 esc. 2 y 3 extremo a extremo: con el artefacto alterado, la API
responde 503 (nunca 500), la ingesta y la regla 2-8 °C siguen operando y
/health/detalle informa el motivo del rechazo."""

import shutil
from datetime import datetime, timedelta, timezone

import pytest

from src.infrastructure.ai import random_forest_service as rf
from src.infrastructure.ai.random_forest_service import (
    DEFAULT_METADATA_PATH,
    DEFAULT_MODEL_PATH,
    MOTIVO_MODELO_RECHAZADO,
    RandomForestRiesgoService,
)
from tests.conftest import auth_header

DEVICE_ID = "FARM-HU16-01"


@pytest.fixture
def modelo_alterado(tmp_path, monkeypatch):
    modelo = tmp_path / "random_forest_termico.pkl"
    metadata = tmp_path / "model_metadata.json"
    shutil.copyfile(DEFAULT_MODEL_PATH, modelo)
    shutil.copyfile(DEFAULT_METADATA_PATH, metadata)
    with modelo.open("ab") as archivo:
        archivo.write(b"alterado")
    servicio = RandomForestRiesgoService(model_path=modelo, metadata_path=metadata)
    monkeypatch.setattr(rf, "_instancia", servicio)
    return servicio


def _lectura(minutos: int, temperatura: float) -> dict:
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    return {
        "device_id": DEVICE_ID,
        "timestamp": (base + timedelta(minutes=minutos)).isoformat(),
        "temperatura_interna": temperatura,
        "temperatura_ambiental": 21.0,
        "humedad_ambiental": 55.0,
        "apertura_refrigerador": False,
        "estado_conectividad": "online",
    }


def test_metadata_del_modelo_alterado_es_503_no_500(client, token_farmaceutico, modelo_alterado):
    respuesta = client.get("/api/ia/modelo", headers=auth_header(token_farmaceutico))

    assert respuesta.status_code == 503
    assert "verificación" in respuesta.json()["detail"]


def test_clasificar_con_modelo_alterado_responde_sin_modelo(client, token_farmaceutico, modelo_alterado):
    vector = {
        "temperatura_ambiental": 21.0, "humedad_ambiental": 55.0, "temperatura_interna": 5.0,
        "diferencia_sensores": 16.0, "duracion_fuera_rango": 0.0, "frecuencia_desviaciones": 0.0,
        "tendencia_termica": 0.0, "apertura_refrigerador": False, "hora_evento": 12,
        "estado_conectividad_online": True,
    }
    respuesta = client.post("/api/ia/clasificar", json=vector, headers=auth_header(token_farmaceutico))

    assert respuesta.status_code == 200
    assert respuesta.json()["estado_inferencia"] == "modelo_no_disponible"
    assert respuesta.json()["confianza"] is None


def test_la_ingesta_y_la_excursion_siguen_con_el_modelo_alterado(client, token_tecnico, modelo_alterado):
    respuestas = [
        client.post("/api/lecturas", json=_lectura(m, t), headers=auth_header(token_tecnico))
        for m, t in ((0, 5.0), (5, 5.0), (10, 5.0), (15, 9.5))
    ]

    assert [r.status_code for r in respuestas] == [201, 201, 201, 201]
    en_rango, fuera = respuestas[2].json(), respuestas[3].json()
    assert en_rango["estado_inferencia"] == "modelo_no_disponible"
    assert en_rango["motivo_no_inferencia"] == MOTIVO_MODELO_RECHAZADO
    assert en_rango["modelo_version"] is None and en_rango["confianza_ia"] is None
    # HU-16 esc. 3: la regla directa 2-8 °C y la alerta crítica no dependen de la IA.
    assert fuera["excursion_confirmada"] is True
    assert fuera["riesgo_efectivo"] == "excursion_critica"
    alertas = client.get("/api/alertas", headers=auth_header(token_tecnico)).json()
    assert any(a["device_id"] == DEVICE_ID and a["nivel_riesgo"] == "excursion_critica" for a in alertas)


def test_health_detalle_informa_el_motivo_del_rechazo(client, token_admin, modelo_alterado):
    cuerpo = client.get("/health/detalle", headers=auth_header(token_admin)).json()

    assert cuerpo["status"] == "degradado"
    assert cuerpo["modelo_ia"]["disponible"] is False
    assert cuerpo["modelo_ia"]["version"] is None
    assert "Checksum" in cuerpo["modelo_ia"]["motivo_rechazo"]
    assert cuerpo["modelo_ia"]["modo_legado"] == "experimental"


def test_health_detalle_con_el_modelo_oficial(client, token_admin):
    cuerpo = client.get("/health/detalle", headers=auth_header(token_admin)).json()

    assert cuerpo["status"] == "ok"
    modelo = cuerpo["modelo_ia"]
    assert modelo["disponible"] is True
    assert modelo["motivo_rechazo"] is None
    assert modelo["version"]
    assert modelo["preventivo"]["modo"] == "desactivado"
    assert modelo["preventivo"]["motivo_rechazo"] is None
