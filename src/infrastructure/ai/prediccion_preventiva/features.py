"""Extractor ÚNICO de features causales, compartido por entrenamiento e inferencia.

Ambos caminos terminan en `calcular_matriz`, que recibe una matriz de
ventanas (n x K) de la rejilla regular, donde la columna K-1 es el instante
de predicción t y la columna 0 es t - ventana_max. Nada posterior a t entra
en una ventana:

- Entrenamiento: `features_de_rejillas` apila, por segmento continuo, las
  ventanas deslizantes de la rejilla (relleno NaN antes del inicio del
  segmento: una ventana nunca cruza un corte).
- Producción: `features_en_instante` remuestrea el historial crudo de
  lecturas anclando la rejilla en el instante de la lectura actual y llama
  a la misma función con una sola fila.

Las ventanas se definen en minutos y se convierten a puntos de rejilla, así
que no dependen de la frecuencia de muestreo de la fuente (5 s, 60 s, 30 s).

Variables deliberadamente EXCLUIDAS (ver docs/ml/02_PREPROCESSING_AND_FEATURES.md):
humedad (interior en Hohenheim, exterior en ColdCare: no es la misma
magnitud), hora del día (memorizaría el horario del protocolo), identidad
del sensor, del archivo o del dataset.
"""

import warnings

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from src.infrastructure.ai.prediccion_preventiva.calidad import (
    TEMPERATURA_MAX_FISICA_C,
    TEMPERATURA_MIN_FISICA_C,
    remuestrear,
)
from src.infrastructure.ai.prediccion_preventiva.configuracion import (
    CALIDAD_HISTORIAL_INSUFICIENTE,
    CALIDAD_OK,
    ConfiguracionPreventiva,
)

CONJUNTO_TERMICO = "termico_minimo"
CONJUNTO_TERMICO_PUERTA = "termico_puerta"
# HU-17 / ablación del TI §1.4.1: el conjunto térmico más el contexto del SHT31
# (temperatura ambiental y humedad). Se compara contra `termico_minimo` para
# medir si el segundo sensor aporta capacidad preventiva.
CONJUNTO_TERMICO_AMBIENTE = "termico_ambiente"
CONJUNTOS = (CONJUNTO_TERMICO, CONJUNTO_TERMICO_PUERTA, CONJUNTO_TERMICO_AMBIENTE)

# Margen para "cerca del límite": el mismo 1 °C que usa la regla legada
# `reglas_riesgo.MARGEN_PREVENTIVO_C`.
MARGEN_CERCANIA_C = 1.0


def nombres_features(config: ConfiguracionPreventiva, conjunto: str = CONJUNTO_TERMICO) -> list[str]:
    if conjunto not in CONJUNTOS:
        raise ValueError(f"Conjunto de features desconocido: {conjunto}")
    nombres = ["temp_actual", "dist_limite_sup", "dist_limite_inf", "delta_1min"]
    for w in config.ventanas_min:
        nombres += [f"media_{w}m", f"min_{w}m", f"max_{w}m", f"std_{w}m", f"pendiente_{w}m", f"delta_{w}m"]
    w = config.ventana_max_min
    nombres += [
        f"max_subida_{w}m", f"max_bajada_{w}m", f"frac_subidas_{w}m",
        "aceleracion", f"frac_cerca_limite_{w}m", f"frac_fuera_rango_{w}m",
    ]
    if conjunto == CONJUNTO_TERMICO_PUERTA:
        nombres += ["puerta_actual", f"frac_puerta_{w}m"]
    if conjunto == CONJUNTO_TERMICO_AMBIENTE:
        nombres += ["temp_amb_actual", "humedad_actual", "dif_interna_ambiente", f"media_temp_amb_{w}m"]
    return nombres


def _pendiente(sub: np.ndarray, paso_min: float) -> np.ndarray:
    """Pendiente por mínimos cuadrados (°C/min) ignorando NaN, por fila."""
    ancho = sub.shape[1]
    x = (np.arange(ancho) - (ancho - 1)) * paso_min
    m = np.isfinite(sub)
    y = np.where(m, sub, 0.0)
    n = m.sum(axis=1)
    sx = (m * x).sum(axis=1)
    sy = y.sum(axis=1)
    sxx = (m * x * x).sum(axis=1)
    sxy = (y * x).sum(axis=1)
    den = n * sxx - sx**2
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, (n * sxy - sx * sy) / den, np.nan)


def calcular_matriz(
    temperaturas: np.ndarray,
    puertas: np.ndarray | None,
    config: ConfiguracionPreventiva,
    conjunto: str = CONJUNTO_TERMICO,
    ambiente: np.ndarray | None = None,
    humedad: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """(X, ok): X n x f en el orden de `nombres_features`; ok indica las
    filas con historial suficiente. Las filas no ok NO deben predecirse.
    `ambiente` y `humedad` (ventanas del SHT31) solo los usa
    CONJUNTO_TERMICO_AMBIENTE; sin ellos esas filas quedan no ok."""
    k = config.puntos_ventana
    if temperaturas.ndim != 2 or temperaturas.shape[1] != k:
        raise ValueError(f"Se esperaban ventanas de {k} puntos, llegaron {temperaturas.shape}")
    rango = config.rango
    paso_min = config.paso_rejilla_s / 60.0
    actual = temperaturas[:, -1]
    previo = temperaturas[:, -2]
    ok = np.isfinite(actual) & np.isfinite(previo)
    columnas = [actual, rango.maximo_celsius - actual, actual - rango.minimo_celsius, (actual - previo) / paso_min]

    pendientes = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        for w in config.ventanas_min:
            m = w * 60 // config.paso_rejilla_s
            sub = temperaturas[:, k - 1 - m :]
            ok &= np.isfinite(sub).mean(axis=1) >= config.cobertura_min_ventana
            inicio = np.where(np.isfinite(sub[:, 0]), sub[:, 0], sub[:, 1])
            ok &= np.isfinite(inicio)
            pendientes[w] = _pendiente(sub, paso_min)
            columnas += [
                np.nanmean(sub, axis=1),
                np.nanmin(sub, axis=1),
                np.nanmax(sub, axis=1),
                np.nanstd(sub, axis=1),
                pendientes[w],
                actual - inicio,
            ]
        difs = np.diff(temperaturas, axis=1) / paso_min
        validas_dif = np.isfinite(difs)
        validos = np.isfinite(temperaturas)
        cerca = validos & (
            (temperaturas >= rango.maximo_celsius - MARGEN_CERCANIA_C)
            | (temperaturas <= rango.minimo_celsius + MARGEN_CERCANIA_C)
        )
        fuera = validos & ((temperaturas > rango.maximo_celsius) | (temperaturas < rango.minimo_celsius))
        n_validos = np.maximum(validos.sum(axis=1), 1)
        columnas += [
            np.nanmax(difs, axis=1),
            np.nanmin(difs, axis=1),
            (difs > 0).sum(axis=1) / np.maximum(validas_dif.sum(axis=1), 1),
            pendientes[min(config.ventanas_min)] - pendientes[config.ventana_max_min],
            cerca.sum(axis=1) / n_validos,
            fuera.sum(axis=1) / n_validos,
        ]
        if conjunto == CONJUNTO_TERMICO_PUERTA:
            if puertas is None:
                puertas = np.full_like(temperaturas, np.nan)
            ok &= np.isfinite(puertas[:, -1])
            columnas += [puertas[:, -1], np.nanmean(puertas, axis=1)]
        if conjunto == CONJUNTO_TERMICO_AMBIENTE:
            if ambiente is None:
                ambiente = np.full_like(temperaturas, np.nan)
            if humedad is None:
                humedad = np.full_like(temperaturas, np.nan)
            ok &= np.isfinite(ambiente[:, -1]) & np.isfinite(humedad[:, -1])
            columnas += [ambiente[:, -1], humedad[:, -1], actual - ambiente[:, -1], np.nanmean(ambiente, axis=1)]

    x = np.column_stack(columnas).astype(float)
    ok &= np.isfinite(x).all(axis=1)
    return x, ok


def matriz_ventanas(valores: np.ndarray, k: int) -> np.ndarray:
    relleno = np.concatenate([np.full(k - 1, np.nan), valores.astype(float)])
    return sliding_window_view(relleno, k)


def features_de_rejillas(
    rejillas: pd.DataFrame, config: ConfiguracionPreventiva, conjunto: str = CONJUNTO_TERMICO
) -> tuple[np.ndarray, np.ndarray]:
    """Features para TODOS los puntos de `rejillas` (mismo orden de filas).
    Las ventanas se construyen dentro de cada segmento."""
    k = config.puntos_ventana
    x = np.full((len(rejillas), len(nombres_features(config, conjunto))), np.nan)
    ok = np.zeros(len(rejillas), dtype=bool)
    posiciones = np.arange(len(rejillas))
    for _, indices in rejillas.groupby("segmento_id", sort=False).indices.items():
        orden = indices[np.argsort(rejillas["g_ns"].to_numpy()[indices], kind="stable")]
        temps = matriz_ventanas(rejillas["temperatura"].to_numpy()[orden], k)
        puertas = matriz_ventanas(rejillas["puerta"].to_numpy()[orden], k)
        contexto = {
            nombre: matriz_ventanas(rejillas[columna].to_numpy()[orden], k)
            for nombre, columna in (("ambiente", "temp_ambiental"), ("humedad", "humedad"))
            if columna in rejillas.columns
        }
        xs, oks = calcular_matriz(temps, puertas, config, conjunto, **contexto)
        x[posiciones[orden]] = xs
        ok[posiciones[orden]] = oks
    return x, ok


def features_en_instante(
    ts_ns: np.ndarray,
    temperaturas: np.ndarray,
    puertas: np.ndarray | None,
    t_eval_ns: int,
    config: ConfiguracionPreventiva,
    conjunto: str = CONJUNTO_TERMICO,
) -> tuple[np.ndarray | None, str]:
    """Vector de features en el instante t_eval a partir de lecturas crudas
    (cualquier frecuencia). Devuelve (vector, calidad). Con historial
    insuficiente devuelve (None, HISTORIAL_INSUFICIENTE): nunca rellena."""
    paso_ns = config.paso_rejilla_s * 1_000_000_000
    k = config.puntos_ventana
    temperaturas = np.asarray(temperaturas, dtype=float).copy()
    temperaturas[(temperaturas < TEMPERATURA_MIN_FISICA_C) | (temperaturas > TEMPERATURA_MAX_FISICA_C)] = np.nan
    ts_ns = np.asarray(ts_ns, dtype=np.int64)
    pasado = ts_ns <= t_eval_ns
    rejilla = remuestrear(
        ts_ns[pasado],
        temperaturas[pasado],
        None if puertas is None else np.asarray(puertas, dtype=float)[pasado],
        config.paso_rejilla_s,
        config.agregacion_s,
        ancla_ns=t_eval_ns,
        desde_ns=t_eval_ns - (k - 1) * paso_ns,
        hasta_ns=t_eval_ns,
    )
    x, ok = calcular_matriz(
        rejilla["temperatura"].to_numpy()[None, :], rejilla["puerta"].to_numpy()[None, :], config, conjunto
    )
    if not ok[0]:
        return None, CALIDAD_HISTORIAL_INSUFICIENTE
    return x[0], CALIDAD_OK
