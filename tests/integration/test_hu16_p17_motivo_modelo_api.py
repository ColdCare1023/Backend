"""P1-7 (HU-16): GET /api/ia/modelo responde 503 en los dos casos, pero el
cuerpo dice con un código estable si el modelo fue RECHAZADO por verificación
o si simplemente NO está entrenado. `detail` no cambia (compatibilidad)."""

import shutil

import joblib
import pytest

from src.infrastructure.ai import random_forest_service as rf
from src.infrastructure.ai.random_forest_service import (
    DEFAULT_METADATA_PATH,
    DEFAULT_MODEL_PATH,
    RandomForestRiesgoService,
)
from tests.conftest import auth_header


class _Estimador:
    n_features_in_ = 10
    classes_ = ["normal", "riesgo_preventivo"]


@pytest.fixture
def servicio(monkeypatch):
    def _usar(instancia: RandomForestRiesgoService) -> None:
        monkeypatch.setattr(rf, "_instancia", instancia)

    return _usar


def test_alterado_es_503_rechazado_por_verificacion(client, token_farmaceutico, servicio, tmp_path):
    modelo = tmp_path / "random_forest_termico.pkl"
    shutil.copyfile(DEFAULT_MODEL_PATH, modelo)
    shutil.copyfile(DEFAULT_METADATA_PATH, tmp_path / "model_metadata.json")
    with modelo.open("ab") as archivo:
        archivo.write(b"x")
    servicio(RandomForestRiesgoService(model_path=modelo))

    respuesta = client.get("/api/ia/modelo", headers=auth_header(token_farmaceutico))

    assert respuesta.status_code == 503
    cuerpo = respuesta.json()
    assert "verificación" in cuerpo["detail"]
    assert cuerpo["motivo"] == "checksum_invalido"
    assert cuerpo["rechazado_por_verificacion"] is True


def test_v2_sin_verificacion_es_503_rechazado(client, token_farmaceutico, servicio, tmp_path):
    ruta = tmp_path / "random_forest_termico.pkl"
    joblib.dump({"modelo": _Estimador(), "metadata": {"model_hash": "x"}}, ruta)
    servicio(RandomForestRiesgoService(model_path=ruta))

    cuerpo = client.get("/api/ia/modelo", headers=auth_header(token_farmaceutico)).json()

    assert cuerpo["motivo"] == "sin_verificacion_de_integridad"
    assert cuerpo["rechazado_por_verificacion"] is True


def test_sin_artefacto_es_503_no_entrenado(client, token_farmaceutico, servicio, tmp_path):
    servicio(RandomForestRiesgoService(model_path=tmp_path / "no_existe.pkl"))

    respuesta = client.get("/api/ia/modelo", headers=auth_header(token_farmaceutico))

    assert respuesta.status_code == 503
    cuerpo = respuesta.json()
    assert cuerpo["detail"] == "El modelo Random Forest no está entrenado en este entorno."
    assert cuerpo["motivo"] == "no_entrenado"
    assert cuerpo["rechazado_por_verificacion"] is False


def test_health_detalle_expone_el_codigo(client, token_admin, servicio, tmp_path):
    ruta = tmp_path / "random_forest_termico.pkl"
    joblib.dump(_Estimador(), ruta)
    servicio(RandomForestRiesgoService(model_path=ruta))

    modelo = client.get("/health/detalle", headers=auth_header(token_admin)).json()["modelo_ia"]

    assert modelo["disponible"] is False
    assert modelo["motivo_codigo"] == "sin_verificacion_de_integridad"
