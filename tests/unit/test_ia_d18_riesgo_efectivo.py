"""IA-D18 (P-8): la regla 2-8 °C decide el riesgo efectivo siempre que el
modelo no da clase. Una lectura válida en rango sin clase no puede quedar con
`riesgo_efectivo=None`: sin él no se gestiona el episodio de alerta."""

from datetime import datetime, timezone

import pytest

from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.value_objects.nivel_riesgo import NivelRiesgo


def _lectura(temperatura: float | None, clase: NivelRiesgo | None) -> LecturaTermica:
    lectura = LecturaTermica(
        device_id="FARM-D18", timestamp=datetime(2026, 11, 1, tzinfo=timezone.utc),
        temperatura_ambiental=21.0, humedad_ambiental=50.0, temperatura_interna=temperatura,
        apertura_refrigerador=False, estado_conectividad="online",
    )
    lectura.nivel_riesgo = clase
    return lectura


@pytest.mark.parametrize("temperatura", [2.0, 5.0, 8.0])
def test_en_rango_sin_clase_del_modelo_es_normal_por_la_regla(temperatura):
    assert _lectura(temperatura, None).calcular_riesgo_efectivo() == NivelRiesgo.NORMAL


@pytest.mark.parametrize("clase", [NivelRiesgo.NORMAL, NivelRiesgo.RIESGO_PREVENTIVO])
def test_en_rango_con_clase_del_modelo_manda_el_modelo(clase):
    assert _lectura(7.5, clase).calcular_riesgo_efectivo() == clase


@pytest.mark.parametrize("temperatura", [1.9, 8.1, 9.0, -10.0])
@pytest.mark.parametrize("clase", [None, NivelRiesgo.NORMAL, NivelRiesgo.RIESGO_PREVENTIVO])
def test_fuera_de_rango_siempre_es_excursion_critica(temperatura, clase):
    assert _lectura(temperatura, clase).calcular_riesgo_efectivo() == NivelRiesgo.EXCURSION_CRITICA


def test_sin_temperatura_interna_queda_sin_resolver():
    assert _lectura(None, None).calcular_riesgo_efectivo() is None
