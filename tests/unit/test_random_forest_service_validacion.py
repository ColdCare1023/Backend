"""Pruebas de los hallazgos AI-03/AI-04/AI-05 (auditoría de IA): el servicio
debe validar compatibilidad de features/clases al cargar el modelo, y rechazar
artefactos incompatibles con un error claro en vez de fallar de forma confusa
en la primera inferencia real."""

import joblib
import numpy as np
import pytest

from src.infrastructure.ai.random_forest_service import RandomForestRiesgoService


class _ModeloFeaturesIncorrectas:
    n_features_in_ = 3  # el backend construye 10 features
    classes_ = ["normal", "riesgo_preventivo", "excursion_critica"]

    def predict_proba(self, x):
        return np.array([[1.0, 0.0, 0.0]])


class _ModeloClasesDesconocidas:
    n_features_in_ = 10
    classes_ = ["normal", "riesgo_preventivo", "clase_inventada"]

    def predict_proba(self, x):
        return np.array([[1.0, 0.0, 0.0]])


def test_rechaza_modelo_con_numero_de_features_incorrecto(tmp_path):
    ruta = tmp_path / "modelo_malo.pkl"
    joblib.dump(
        {"modelo": _ModeloFeaturesIncorrectas(), "metadata": {"feature_names": None, "model_hash": "x"}},
        ruta,
    )
    servicio = RandomForestRiesgoService(model_path=ruta)
    with pytest.raises(RuntimeError, match="features"):
        servicio.modelo_disponible


def test_rechaza_modelo_con_clases_desconocidas(tmp_path):
    ruta = tmp_path / "modelo_malo.pkl"
    joblib.dump(
        {"modelo": _ModeloClasesDesconocidas(), "metadata": {"feature_names": None, "model_hash": "x"}},
        ruta,
    )
    servicio = RandomForestRiesgoService(model_path=ruta)
    with pytest.raises(RuntimeError, match="clases desconocidas"):
        servicio.modelo_disponible


def test_rechaza_modelo_con_orden_de_features_distinto(tmp_path):
    ruta = tmp_path / "modelo_malo.pkl"
    orden_incorrecto = ["temperatura_interna", "temperatura_ambiental"]  # orden/tamaño distinto
    joblib.dump(
        {"modelo": _ModeloClasesDesconocidas(), "metadata": {"feature_names": orden_incorrecto}},
        ruta,
    )
    servicio = RandomForestRiesgoService(model_path=ruta)
    with pytest.raises(RuntimeError, match="orden de features"):
        servicio.modelo_disponible


def test_modelo_oficial_actual_pasa_la_validacion_de_compatibilidad():
    """Control: el artefacto oficial real (v2 corregido) debe cargar sin errores."""
    servicio = RandomForestRiesgoService()
    assert servicio.modelo_disponible is True
    assert servicio.metadata["model_hash"]
    assert servicio.metadata["feature_names"] is not None


def test_rechaza_artefacto_con_checksum_externo_alterado(tmp_path):
    ruta = tmp_path / "modelo.pkl"
    metadata = tmp_path / "model_metadata.json"
    joblib.dump(_ModeloFeaturesIncorrectas(), ruta)
    metadata.write_text('{"model_hash": "checksum-falso"}')

    servicio = RandomForestRiesgoService(model_path=ruta, metadata_path=metadata)
    with pytest.raises(RuntimeError, match="Checksum SHA-256 inválido"):
        _ = servicio.modelo_disponible
