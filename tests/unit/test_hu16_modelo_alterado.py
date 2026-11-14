"""HU-16 esc. 2 y 3 / HU-46 esc. 3: un artefacto alterado o que no es el de
la versión activa deja el modelo «no disponible» con su motivo. Nunca lanza
(no puede convertirse en un 500 ni tumbar la ingesta) y no se vuelve a leer
ni a hashear el archivo en cada lectura."""

import json
import logging
import shutil

import pytest

from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.ai import random_forest_service as rf
from src.infrastructure.ai.features import FeaturesRiesgoTermico
from src.infrastructure.ai.random_forest_service import (
    DEFAULT_METADATA_PATH,
    DEFAULT_MODEL_PATH,
    ESTADO_MODELO_NO_DISPONIBLE,
    MOTIVO_MODELO_RECHAZADO,
    ORIGEN_REGLA_SALVAGUARDA,
    RandomForestRiesgoService,
)

FEATURES = FeaturesRiesgoTermico(
    temperatura_ambiental=21.0, humedad_ambiental=55.0, temperatura_interna=5.0, diferencia_sensores=16.0,
    duracion_fuera_rango=0.0, frecuencia_desviaciones=0.0, tendencia_termica=0.0,
    apertura_refrigerador=False, hora_evento=12, estado_conectividad_online=True,
)


@pytest.fixture
def artefacto_alterado(tmp_path):
    """Copia del artefacto oficial con un byte añadido: su SHA-256 ya no es
    el que declara model_metadata.json."""
    modelo = tmp_path / "random_forest_termico.pkl"
    metadata = tmp_path / "model_metadata.json"
    shutil.copyfile(DEFAULT_MODEL_PATH, modelo)
    shutil.copyfile(DEFAULT_METADATA_PATH, metadata)
    with modelo.open("ab") as archivo:
        archivo.write(b"\x00")
    return modelo, metadata


def test_artefacto_alterado_queda_no_disponible_sin_lanzar(artefacto_alterado):
    modelo, metadata = artefacto_alterado
    servicio = RandomForestRiesgoService(model_path=modelo, metadata_path=metadata)

    assert servicio.modelo_disponible is False
    assert "Checksum SHA-256 inválido" in servicio.motivo_rechazo()
    assert servicio.metadata is None


def test_inferencia_con_artefacto_alterado_no_usa_el_archivo_y_sigue_la_regla(artefacto_alterado):
    modelo, metadata = artefacto_alterado
    servicio = RandomForestRiesgoService(model_path=modelo, metadata_path=metadata)

    resultado = servicio.inferir(FEATURES)

    assert resultado.estado_inferencia == ESTADO_MODELO_NO_DISPONIBLE
    assert resultado.motivo_no_inferencia == MOTIVO_MODELO_RECHAZADO
    assert resultado.origen == ORIGEN_REGLA_SALVAGUARDA
    assert resultado.confianza is None and resultado.probabilidades_por_clase is None
    assert resultado.nivel == NivelRiesgo.NORMAL  # salvaguarda determinista, no el modelo


def test_el_rechazo_se_recuerda_y_no_se_rehashea_en_cada_lectura(artefacto_alterado, monkeypatch):
    modelo, metadata = artefacto_alterado
    servicio = RandomForestRiesgoService(model_path=modelo, metadata_path=metadata)
    lecturas_de_disco = []
    original = RandomForestRiesgoService._leer_y_validar

    def _contar(self):
        lecturas_de_disco.append(1)
        return original(self)

    monkeypatch.setattr(RandomForestRiesgoService, "_leer_y_validar", _contar)

    for _ in range(25):
        servicio.inferir(FEATURES)
    assert servicio.motivo_rechazo() is not None
    assert len(lecturas_de_disco) == 1


def test_el_rechazo_se_registra_como_error_critico(artefacto_alterado, caplog, monkeypatch):
    modelo, metadata = artefacto_alterado
    servicio = RandomForestRiesgoService(model_path=modelo, metadata_path=metadata)
    # alembic.fileConfig (test_migraciones) deshabilita los loggers existentes.
    monkeypatch.setattr(rf.logger, "disabled", False)

    with caplog.at_level(logging.CRITICAL, logger="infrastructure.ai.random_forest_service"):
        servicio.motivo_rechazo()

    criticos = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(criticos) == 1
    assert "rechazado" in criticos[0].getMessage()


def test_hash_de_version_activa_distinto_rechaza_y_fijar_el_correcto_lo_recupera():
    servicio = RandomForestRiesgoService()
    servicio.fijar_hash_aprobado("b" * 64)
    assert servicio.modelo_disponible is False
    assert "versión activa" in servicio.motivo_rechazo()
    assert servicio.inferir(FEATURES).motivo_no_inferencia == MOTIVO_MODELO_RECHAZADO

    # Activar la versión cuyo hash SÍ es el del artefacto vuelve a evaluarlo.
    servicio.fijar_hash_aprobado(servicio.hash_artefacto())
    assert servicio.motivo_rechazo() is None
    assert servicio.modelo_disponible is True


def test_metadata_ilegible_tambien_es_un_rechazo_controlado(tmp_path):
    modelo = tmp_path / "random_forest_termico.pkl"
    metadata = tmp_path / "model_metadata.json"
    shutil.copyfile(DEFAULT_MODEL_PATH, modelo)
    metadata.write_text("{no es json", encoding="utf-8")

    servicio = RandomForestRiesgoService(model_path=modelo, metadata_path=metadata)

    assert servicio.modelo_disponible is False
    assert servicio.motivo_rechazo()


def test_sin_artefacto_no_es_rechazo():
    servicio = RandomForestRiesgoService(model_path=DEFAULT_MODEL_PATH.with_name("no_existe.pkl"))
    assert servicio.modelo_disponible is False
    assert servicio.motivo_rechazo() is None
    assert servicio.inferir(FEATURES).motivo_no_inferencia == "modelo_no_cargado_en_este_entorno"


def test_estado_para_health_resume_sin_rutas(artefacto_alterado):
    modelo, metadata = artefacto_alterado
    estado = RandomForestRiesgoService(model_path=modelo, metadata_path=metadata).estado()

    assert estado["disponible"] is False
    assert estado["version"] is None
    assert "Checksum" in estado["motivo_rechazo"]
    assert estado["artefacto_presente"] is True
    assert str(modelo) not in json.dumps(estado)


def test_estado_del_modelo_oficial():
    estado = RandomForestRiesgoService().estado()
    declarada = json.loads(DEFAULT_METADATA_PATH.read_text(encoding="utf-8"))

    assert estado["disponible"] is True
    assert estado["motivo_rechazo"] is None
    assert estado["version"] == declarada["model_version"]
    assert estado["sklearn_entrenamiento"] == declarada["sklearn_version"]
    assert estado["sklearn_instalado"]


def test_la_instancia_global_es_unica():
    assert rf.get_random_forest_service() is rf.get_random_forest_service()
