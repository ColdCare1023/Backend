"""HU-38: el reporte BPA cuenta las excursiones por riesgo efectivo.

El Random Forest es binario (normal/riesgo_preventivo): contar por la clase
cruda daba siempre cero excursiones críticas aunque el refrigerador hubiera
salido de 2-8 °C."""

from datetime import datetime, timezone

from src.application.use_cases.exportar_reporte_bpa_pdf import _calcular_estadisticas
from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.value_objects.nivel_riesgo import NivelRiesgo


def _lectura(temperatura: float, clase: NivelRiesgo | None) -> LecturaTermica:
    lectura = LecturaTermica(
        device_id="FARM-01", timestamp=datetime(2026, 10, 1, tzinfo=timezone.utc),
        temperatura_ambiental=21.0, humedad_ambiental=50.0, temperatura_interna=temperatura,
        apertura_refrigerador=False, estado_conectividad="online",
    )
    lectura.nivel_riesgo = clase
    lectura.excursion_confirmada = lectura.es_excursion_confirmada()
    lectura.riesgo_efectivo = lectura.calcular_riesgo_efectivo()
    return lectura


def test_cuenta_la_excursion_aunque_el_modelo_diga_preventivo():
    estadisticas = _calcular_estadisticas([
        _lectura(5.0, NivelRiesgo.NORMAL),
        _lectura(9.4, NivelRiesgo.RIESGO_PREVENTIVO),
        _lectura(10.1, None),  # no_clasificable: la regla de rango igual la confirma
    ])

    assert estadisticas["alertas_criticas"] == 2
    assert estadisticas["por_nivel"] == {"normal": 1, "excursion_critica": 2}


def test_lectura_antigua_sin_riesgo_efectivo_conserva_su_clase():
    antigua = _lectura(7.7, NivelRiesgo.RIESGO_PREVENTIVO)
    antigua.riesgo_efectivo = None

    assert _calcular_estadisticas([antigua])["por_nivel"] == {"riesgo_preventivo": 1}
