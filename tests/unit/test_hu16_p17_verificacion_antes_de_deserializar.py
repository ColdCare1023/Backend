"""HU-16 esc. 1-2 / HU-46 esc. 3 (P1-7 de la validación final).

joblib/pickle ejecuta código al deserializar. Un artefacto v1 (estimador a
secas) o v2 (dict con metadata embebida) que no tiene metadata externa NI una
versión activa registrada no tiene contra qué verificar su SHA-256: no se
deserializa. Y el motivo viaja como código estable, para que la interfaz
distinga «rechazado por verificación» de «no entrenado».
"""

import shutil

import joblib
import numpy as np
import pytest

from src.infrastructure.ai import random_forest_service as rf
from src.infrastructure.ai.random_forest_service import (
    CODIGO_CHECKSUM_INVALIDO,
    CODIGO_HASH_VERSION_ACTIVA,
    CODIGO_INCOMPATIBLE,
    CODIGO_NO_ENTRENADO,
    CODIGO_SIN_VERIFICACION,
    CODIGOS_RECHAZO,
    DEFAULT_METADATA_PATH,
    DEFAULT_MODEL_PATH,
    RandomForestRiesgoService,
)


class _EstimadorCompatible:
    n_features_in_ = 10
    classes_ = np.array(["normal", "riesgo_preventivo"])

    def predict_proba(self, x):
        return np.array([[0.9, 0.1]])


class _EstimadorIncompatible:
    n_features_in_ = 3
    classes_ = np.array(["normal", "riesgo_preventivo"])

    def predict_proba(self, x):
        return np.array([[0.9, 0.1]])


@pytest.fixture
def contar_cargas(monkeypatch):
    llamadas = []
    original = rf.joblib.load

    def _load(ruta, *args, **kwargs):
        llamadas.append(ruta)
        return original(ruta, *args, **kwargs)

    monkeypatch.setattr(rf.joblib, "load", _load)
    return llamadas


@pytest.mark.parametrize(
    "contenido",
    [_EstimadorCompatible(), {"modelo": _EstimadorCompatible(), "metadata": {"model_hash": "x"}}],
    ids=["v1_estimador", "v2_metadata_embebida"],
)
def test_sin_metadata_externa_ni_version_activa_no_se_deserializa(tmp_path, contar_cargas, contenido):
    ruta = tmp_path / "random_forest_termico.pkl"
    joblib.dump(contenido, ruta)
    servicio = RandomForestRiesgoService(model_path=ruta)

    assert servicio.modelo_disponible is False
    assert contar_cargas == [], "el artefacto no verificado no debe llegar a joblib.load"
    assert servicio.motivo_codigo() == CODIGO_SIN_VERIFICACION
    assert "no se deserializa" in servicio.motivo_rechazo()


def test_con_la_version_activa_registrada_el_v1_si_se_carga(tmp_path, contar_cargas):
    ruta = tmp_path / "random_forest_termico.pkl"
    joblib.dump(_EstimadorCompatible(), ruta)
    servicio = RandomForestRiesgoService(model_path=ruta)
    servicio.fijar_hash_aprobado(servicio.hash_artefacto())

    assert servicio.modelo_disponible is True
    assert len(contar_cargas) == 1
    assert servicio.motivo_codigo() is None


def test_el_modelo_oficial_v3_sigue_cargando_sin_version_activa():
    """Producción hoy: no hay versión activa y el v3 se verifica con su metadata externa."""
    servicio = RandomForestRiesgoService()
    assert servicio.hash_aprobado is None
    assert servicio.modelo_disponible is True
    assert servicio.motivo_codigo() is None


def test_los_codigos_distinguen_no_entrenado_de_rechazado(tmp_path):
    ausente = RandomForestRiesgoService(model_path=tmp_path / "no_existe.pkl")
    assert ausente.motivo_codigo() == CODIGO_NO_ENTRENADO
    assert CODIGO_NO_ENTRENADO not in CODIGOS_RECHAZO
    assert ausente.motivo_rechazo() is None

    alterado = tmp_path / "alterado" / "random_forest_termico.pkl"
    alterado.parent.mkdir()
    shutil.copyfile(DEFAULT_MODEL_PATH, alterado)
    shutil.copyfile(DEFAULT_METADATA_PATH, alterado.with_name("model_metadata.json"))
    with alterado.open("ab") as archivo:
        archivo.write(b"\x00")
    assert RandomForestRiesgoService(model_path=alterado).motivo_codigo() == CODIGO_CHECKSUM_INVALIDO

    otra_version = RandomForestRiesgoService()
    otra_version.fijar_hash_aprobado("e" * 64)
    assert otra_version.motivo_codigo() == CODIGO_HASH_VERSION_ACTIVA

    incompatible = tmp_path / "incompatible.pkl"
    joblib.dump(_EstimadorIncompatible(), incompatible)
    servicio = RandomForestRiesgoService(model_path=incompatible)
    servicio.fijar_hash_aprobado(servicio.hash_artefacto())
    assert servicio.motivo_codigo() == CODIGO_INCOMPATIBLE
    assert {CODIGO_CHECKSUM_INVALIDO, CODIGO_HASH_VERSION_ACTIVA, CODIGO_INCOMPATIBLE} <= CODIGOS_RECHAZO


def test_el_estado_para_health_incluye_el_codigo(tmp_path):
    ruta = tmp_path / "random_forest_termico.pkl"
    joblib.dump(_EstimadorCompatible(), ruta)

    estado = RandomForestRiesgoService(model_path=ruta).estado()

    assert estado["disponible"] is False
    assert estado["motivo_codigo"] == CODIGO_SIN_VERIFICACION
    assert RandomForestRiesgoService().estado()["motivo_codigo"] is None
