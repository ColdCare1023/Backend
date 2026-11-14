"""HU-34 esc. 4: sin excursión confirmada, una lectura sin clasificación del
modelo o con el DS18B20 en falla no se presenta como Normal, Riesgo
Preventivo ni Excursión Crítica."""

from datetime import datetime, timezone

import pytest

from src.domain.entities.lectura_termica import (
    ESTADO_RIESGO_FALLA_SENSOR,
    ESTADO_RIESGO_SIN_CLASIFICACION_IA,
    ESTADOS_RIESGO,
    LecturaTermica,
)
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.interface.api.mappers import lectura_to_response
from src.interface.api.schemas import LecturaResponse


def _lectura(temperatura=5.0, **campos) -> LecturaTermica:
    lectura = LecturaTermica(
        device_id="HU34", timestamp=datetime(2026, 11, 1, tzinfo=timezone.utc), temperatura_ambiental=21.0,
        humedad_ambiental=55.0, temperatura_interna=temperatura, apertura_refrigerador=False,
        estado_conectividad="online",
    )
    for nombre, valor in campos.items():
        setattr(lectura, nombre, valor)
    lectura.excursion_confirmada = lectura.es_excursion_confirmada()
    return lectura


COMPLETADA = {"estado_inferencia": "completada", "origen_clasificacion": "random_forest"}


@pytest.mark.parametrize(
    ("campos", "esperado"),
    [
        # Esc. 1 y 2: hay clasificación del modelo.
        ({**COMPLETADA, "nivel_riesgo": NivelRiesgo.NORMAL, "riesgo_efectivo": NivelRiesgo.NORMAL}, "normal"),
        (
            {**COMPLETADA, "nivel_riesgo": NivelRiesgo.RIESGO_PREVENTIVO,
             "riesgo_efectivo": NivelRiesgo.RIESGO_PREVENTIVO},
            "riesgo_preventivo",
        ),
        # Esc. 4: sin ventana histórica, la regla deja riesgo_efectivo=normal…
        (
            {"estado_inferencia": "omitida", "origen_clasificacion": "dato_insuficiente",
             "motivo_no_inferencia": "historial_insuficiente_para_tendencia",
             "riesgo_efectivo": NivelRiesgo.NORMAL},
            ESTADO_RIESGO_SIN_CLASIFICACION_IA,
        ),
        # …y lo mismo con el modelo no disponible, rechazado o fallido.
        (
            {"estado_inferencia": "modelo_no_disponible", "nivel_riesgo": NivelRiesgo.NORMAL,
             "riesgo_efectivo": NivelRiesgo.NORMAL},
            ESTADO_RIESGO_SIN_CLASIFICACION_IA,
        ),
        ({"estado_inferencia": "fallida", "riesgo_efectivo": NivelRiesgo.NORMAL}, ESTADO_RIESGO_SIN_CLASIFICACION_IA),
        (
            {"estado_inferencia": "omitida", "motivo_no_inferencia": "time_quality_insuficiente",
             "riesgo_efectivo": NivelRiesgo.NORMAL},
            ESTADO_RIESGO_SIN_CLASIFICACION_IA,
        ),
    ],
)
def test_estado_en_rango(campos, esperado):
    assert _lectura(5.0, **campos).estado_riesgo() == esperado


def test_falla_del_ds18b20_es_falla_de_sensor():
    lectura = _lectura(None, estado_inferencia="omitida", origen_clasificacion="fallo_sensor")
    assert lectura.estado_riesgo() == ESTADO_RIESGO_FALLA_SENSOR


@pytest.mark.parametrize("temperatura", [1.5, 9.5])
def test_la_excursion_confirmada_manda_sin_importar_la_ia(temperatura):
    lectura = _lectura(
        temperatura, estado_inferencia="omitida", motivo_no_inferencia="regla_directa_fuera_de_rango",
        riesgo_efectivo=NivelRiesgo.EXCURSION_CRITICA,
    )
    assert lectura.estado_riesgo() == NivelRiesgo.EXCURSION_CRITICA.value


def test_el_campo_es_aditivo_y_riesgo_efectivo_no_cambia():
    lectura = _lectura(5.0, estado_inferencia="omitida", riesgo_efectivo=NivelRiesgo.NORMAL)
    respuesta = lectura_to_response(lectura)

    assert respuesta.riesgo_efectivo == NivelRiesgo.NORMAL  # compatibilidad
    assert respuesta.estado_riesgo == ESTADO_RIESGO_SIN_CLASIFICACION_IA


def test_el_openapi_publica_los_valores_posibles():
    enum = LecturaResponse.model_json_schema()["properties"]["estado_riesgo"]["anyOf"][0]["enum"]
    assert tuple(enum) == ESTADOS_RIESGO
