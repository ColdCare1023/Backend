"""Etiquetado preventivo, episodios e incidentes independientes."""

import numpy as np
import pandas as pd

from src.infrastructure.ai.prediccion_preventiva import calidad, eventos
from src.infrastructure.ai.prediccion_preventiva.configuracion import ConfiguracionPreventiva
from src.infrastructure.ai.prediccion_preventiva.etiquetas import (
    ESTADO_COBERTURA_INSUFICIENTE,
    ESTADO_EN_EPISODIO,
    ESTADO_FUERA_DE_RANGO,
    ESTADO_HORIZONTE_INCOMPLETO,
    ESTADO_NEGATIVO,
    ESTADO_POSITIVO,
    etiquetar,
)
from tests.unit._preventivo_sintetico import lecturas

CONFIG = ConfiguracionPreventiva()


def _procesar(*series: pd.DataFrame):
    rejillas = calidad.preparar_rejillas(pd.concat(series, ignore_index=True), CONFIG)
    episodios = eventos.agrupar_incidentes(eventos.detectar_episodios(rejillas, CONFIG), CONFIG)
    return etiquetar(rejillas, episodios, CONFIG), episodios


def _minuto(etiquetadas: pd.DataFrame, m: int, serie: str = "S1") -> pd.Series:
    sub = etiquetadas[etiquetadas["serie_id"] == serie].sort_values("g_ns")
    return sub.iloc[m]


def test_excursion_futura_real_etiqueta_positivo_con_anticipacion():
    valores = np.concatenate([np.full(60, 5.0), np.full(10, 9.0), np.full(60, 5.0)])
    etiquetadas, episodios = _procesar(lecturas(valores))
    assert len(episodios) == 1
    inicio_excursion = int(episodios["inicio_ns"].iloc[0])
    sub = etiquetadas.sort_values("g_ns")
    previos = sub[(sub["g_ns"] < inicio_excursion) & (sub["g_ns"] >= inicio_excursion - 30 * 60 * 10**9)]
    assert (previos["estado_etiqueta"] == ESTADO_POSITIVO).all()
    assert previos["minutos_a_excursion"].max() == 30
    lejanos = sub[sub["g_ns"] < inicio_excursion - 30 * 60 * 10**9]
    assert (lejanos["estado_etiqueta"] == ESTADO_NEGATIVO).all()


def test_sin_excursion_futura_etiqueta_negativo():
    etiquetadas, episodios = _procesar(lecturas(np.full(90, 5.0)))
    assert episodios.empty
    assert _minuto(etiquetadas, 10)["estado_etiqueta"] == ESTADO_NEGATIVO


def test_horizonte_incompleto_no_se_etiqueta():
    etiquetadas, _ = _procesar(lecturas(np.full(90, 5.0)))
    final = etiquetadas.sort_values("g_ns").tail(30)
    assert (final["estado_etiqueta"] == ESTADO_HORIZONTE_INCOMPLETO).all()
    assert final["y"].isna().all()


def test_excursion_actual_no_entra_al_clasificador():
    valores = np.concatenate([np.full(40, 5.0), np.full(10, 9.0), np.full(60, 5.0)])
    etiquetadas, _ = _procesar(lecturas(valores))
    fuera = etiquetadas[etiquetadas["temperatura"] > 8]
    assert (fuera["estado_etiqueta"] == ESTADO_FUERA_DE_RANGO).all()
    assert fuera["y"].isna().all()


def test_oscilacion_alrededor_del_limite_es_un_solo_episodio():
    valores = np.concatenate([np.full(40, 5.0), [8.5, 7.9, 8.4, 7.8, 8.6, 8.3], np.full(60, 5.0)])
    etiquetadas, episodios = _procesar(lecturas(valores))
    assert len(episodios) == 1
    dentro = etiquetadas[etiquetadas["temperatura"].between(7.7, 8.0)]
    assert (dentro["estado_etiqueta"] == ESTADO_EN_EPISODIO).all()


def test_eventos_independientes_y_simultaneos():
    valores = np.concatenate([np.full(40, 5.0), np.full(5, 9.0), np.full(40, 5.0), np.full(5, 9.0), np.full(40, 5.0)])
    _, episodios = _procesar(lecturas(valores, serie_id="S1"), lecturas(valores + 0.3, serie_id="S2"))
    # Dos episodios por sensor, separados por más que la tolerancia de fusión...
    assert episodios.groupby("serie_id").size().tolist() == [2, 2]
    # ...pero los dos sensores del mismo recinto suben a la vez: 2 incidentes, no 4.
    assert episodios["incidente_id"].nunique() == 2


def test_mismo_instante_en_recintos_distintos_son_incidentes_distintos():
    valores = np.concatenate([np.full(40, 5.0), np.full(5, 9.0), np.full(40, 5.0)])
    _, episodios = _procesar(lecturas(valores, serie_id="A", entorno_id="farmacia_1"),
                             lecturas(valores, serie_id="B", entorno_id="farmacia_2"))
    assert episodios["incidente_id"].nunique() == 2


def test_cobertura_insuficiente_del_horizonte_no_permite_afirmar_normal():
    df = lecturas(np.full(120, 5.0))
    # Pérdidas cortas repetidas (no cortan el segmento) que dejan el horizonte con < 90 % de datos.
    df = df[~df.index.isin(range(50, 80, 3)) & ~df.index.isin(range(51, 80, 3))]
    etiquetadas, _ = _procesar(df)
    assert (etiquetadas["estado_etiqueta"] == ESTADO_COBERTURA_INSUFICIENTE).any()


def test_arranque_en_caliente_no_es_una_salida_de_rango_observada():
    valores = np.concatenate([np.full(10, 12.0), np.full(60, 5.0)])
    _, episodios = _procesar(lecturas(valores))
    assert len(episodios) == 1
    assert not bool(episodios["inicio_observado"].iloc[0])
    assert eventos.resumen_incidentes(episodios)["inicio_observado"].sum() == 0


def test_excursion_baja_se_tipifica():
    valores = np.concatenate([np.full(40, 4.0), np.full(5, 1.0), np.full(40, 4.0)])
    _, episodios = _procesar(lecturas(valores))
    assert episodios["tipo"].tolist() == ["baja"]
