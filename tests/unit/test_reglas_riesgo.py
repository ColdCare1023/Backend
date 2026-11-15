from datetime import datetime, timezone

import pytest

from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.ai.features import FeaturesRiesgoTermico
from src.infrastructure.ai.reglas_riesgo import clasificar_por_regla


def _features(**overrides) -> FeaturesRiesgoTermico:
    base = {
        "temperatura_ambiental": 6.0,
        "humedad_ambiental": 55.0,
        "temperatura_interna": 5.0,
        "diferencia_sensores": 1.0,
        "duracion_fuera_rango": 0.0,
        "frecuencia_desviaciones": 0.0,
        "tendencia_termica": 0.0,
        "apertura_refrigerador": False,
        "hora_evento": 12,
        "estado_conectividad_online": True,
    }
    base.update(overrides)
    return FeaturesRiesgoTermico(**base)


def test_temperatura_estable_en_el_centro_del_rango_es_normal():
    assert clasificar_por_regla(_features(temperatura_interna=5.0)) == NivelRiesgo.NORMAL


def test_temperatura_cerca_del_limite_es_riesgo_preventivo():
    assert clasificar_por_regla(_features(temperatura_interna=7.5)) == NivelRiesgo.RIESGO_PREVENTIVO


def test_temperatura_fuera_de_rango_breve_es_riesgo_preventivo():
    assert clasificar_por_regla(
        _features(temperatura_interna=9.0, duracion_fuera_rango=2.0)
    ) == NivelRiesgo.RIESGO_PREVENTIVO


def test_temperatura_fuera_de_rango_prolongada_es_riesgo_preventivo_no_excursion():
    """HU-18 criterio 4: el modelo/regla de IA nunca produce excursion_critica
    — esa clase es exclusiva de la regla determinista de rango aplicada
    directamente sobre la temperatura actual, fuera de este pipeline."""
    assert clasificar_por_regla(
        _features(temperatura_interna=12.0, duracion_fuera_rango=45.0)
    ) == NivelRiesgo.RIESGO_PREVENTIVO


def test_temperatura_muy_alejada_del_rango_sigue_siendo_riesgo_preventivo():
    assert clasificar_por_regla(
        _features(temperatura_interna=20.0, duracion_fuera_rango=1.0)
    ) == NivelRiesgo.RIESGO_PREVENTIVO


def test_tendencia_termica_fuerte_dentro_de_rango_es_riesgo_preventivo():
    assert clasificar_por_regla(
        _features(temperatura_interna=5.0, tendencia_termica=2.0)
    ) == NivelRiesgo.RIESGO_PREVENTIVO


@pytest.mark.parametrize("humedad", [99.0, 100.0, 0.0])
def test_hu03_la_humedad_no_asigna_por_si_sola_una_clase_de_riesgo(humedad):
    """HU-03 esc. 2: una HR válida es variable contextual y no asigna por sí
    sola una clase de riesgo térmico (no hay umbral de HR como regla). Con la
    temperatura interna en el centro del rango (5 °C) y la HR en 99 %, ni la
    salvaguarda determinista ni la regla 2-8 °C cambian: NORMAL y sin
    excursión confirmada."""
    assert clasificar_por_regla(_features(temperatura_interna=5.0, humedad_ambiental=humedad)) == NivelRiesgo.NORMAL

    lectura = LecturaTermica(
        device_id="ESP32-HU03", timestamp=datetime(2026, 11, 15, 12, tzinfo=timezone.utc),
        temperatura_ambiental=6.0, humedad_ambiental=humedad, temperatura_interna=5.0,
        apertura_refrigerador=False, estado_conectividad="online",
    )
    assert lectura.es_lectura_valida() is True
    assert lectura.es_excursion_confirmada() is False
    assert lectura.calcular_riesgo_efectivo() == NivelRiesgo.NORMAL
