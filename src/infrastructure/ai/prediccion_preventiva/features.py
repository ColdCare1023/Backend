"""Extractor ÚNICO de features causales, compartido por entrenamiento e inferencia.

Ambos caminos terminan en `calcular_matriz`, que recibe una matriz de
ventanas (n x K) de la rejilla regular, donde la columna K-1 es el instante
de predicción t y la columna 0 es t - ventana_max. Nada posterior a t entra
en una ventana:

- Entrenamiento: `features_de_rejillas` apila, por segmento continuo, las
  ventanas deslizantes de la rejilla (relleno NaN antes del inicio del
  segmento: una ventana nunca cruza un corte).
- Producción: `features_en_instante` remuestrea el historial crudo de
  lecturas anclando la rejilla en el instante de la lectura actual, le aplica
  la MISMA segmentación (`calidad.segmentar`) y llama a la misma función con
  una sola fila.

Las ventanas se definen en minutos y se convierten a puntos de rejilla, así
que no dependen de la frecuencia de muestreo de la fuente (5 s, 60 s, 30 s).

Variables deliberadamente EXCLUIDAS (ver docs/ml/02_PREPROCESSING_AND_FEATURES.md):
humedad en `termico_minimo` y `termico_puerta` (interior en Hohenheim,
exterior en ColdCare: no es la misma magnitud; solo `termico_ambiente`, que
se entrena con corridas de ColdCare, usa la del SHT31), hora del día
(memorizaría el horario del protocolo), identidad del sensor, del archivo o
del dataset.
"""

import warnings
from typing import NamedTuple

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from src.infrastructure.ai.prediccion_preventiva.calidad import (
    TEMPERATURA_MAX_FISICA_C,
    TEMPERATURA_MIN_FISICA_C,
    remuestrear,
    segmentar,
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

# Señales crudas, además de la temperatura interna, que necesita cada conjunto.
# Producción solo calcula los conjuntos de esta tabla (servicio.py rechaza el
# resto): un conjunto nuevo del entrenamiento no se sirve hasta declararlo aquí
# y cubrirlo con la prueba de paridad.
CANAL_PUERTA = "puerta"
CANAL_AMBIENTE = "ambiente"
CANAL_HUMEDAD = "humedad"
CANALES_POR_CONJUNTO: dict[str, tuple[str, ...]] = {
    CONJUNTO_TERMICO: (),
    CONJUNTO_TERMICO_PUERTA: (CANAL_PUERTA,),
    CONJUNTO_TERMICO_AMBIENTE: (CANAL_AMBIENTE, CANAL_HUMEDAD),
}
# Columna de la rejilla (calidad.COLUMNAS_CONTEXTO) de cada canal del SHT31.
_COLUMNA_CONTEXTO = {CANAL_AMBIENTE: "temp_ambiental", CANAL_HUMEDAD: "humedad"}

# Motivos estables de HISTORIAL_INSUFICIENTE (`motivo` de la predicción y
# `motivo_no_inferencia` de la lectura en modo activo).
MOTIVO_SIN_COBERTURA = "ventana_15min_sin_cobertura_suficiente"
# HU-17: la temperatura interna alcanza, pero el conjunto necesita el SHT31 y
# no hay temperatura ambiental o humedad válidas en el punto actual.
MOTIVO_SIN_CONTEXTO_AMBIENTAL = "ventana_15min_sin_contexto_ambiental"
_MOTIVO_SIN_CONTEXTO = {CONJUNTO_TERMICO_AMBIENTE: MOTIVO_SIN_CONTEXTO_AMBIENTAL}

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
    x, ok_termico, ok_contexto = _calcular_matriz(temperaturas, puertas, config, conjunto, ambiente, humedad)
    return x, ok_termico & ok_contexto


def _calcular_matriz(
    temperaturas: np.ndarray,
    puertas: np.ndarray | None,
    config: ConfiguracionPreventiva,
    conjunto: str,
    ambiente: np.ndarray | None,
    humedad: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(X, ok_termico, ok_contexto). ok_termico: la temperatura interna cumple
    las reglas de cobertura y sus features son finitas. ok_contexto: la señal
    adicional del conjunto (puerta o SHT31) está en el punto actual y sus
    features son finitas. `calcular_matriz` exige ambas; separarlas solo sirve
    para informar el motivo cuando no se predice."""
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
        n_termicas = len(columnas)
        ok_contexto = np.ones(temperaturas.shape[0], dtype=bool)
        if conjunto == CONJUNTO_TERMICO_PUERTA:
            if puertas is None:
                puertas = np.full_like(temperaturas, np.nan)
            ok_contexto &= np.isfinite(puertas[:, -1])
            columnas += [puertas[:, -1], np.nanmean(puertas, axis=1)]
        if conjunto == CONJUNTO_TERMICO_AMBIENTE:
            if ambiente is None:
                ambiente = np.full_like(temperaturas, np.nan)
            if humedad is None:
                humedad = np.full_like(temperaturas, np.nan)
            ok_contexto &= np.isfinite(ambiente[:, -1]) & np.isfinite(humedad[:, -1])
            columnas += [ambiente[:, -1], humedad[:, -1], actual - ambiente[:, -1], np.nanmean(ambiente, axis=1)]

    x = np.column_stack(columnas).astype(float)
    finitas = np.isfinite(x)
    ok &= finitas[:, :n_termicas].all(axis=1)
    ok_contexto &= finitas[:, n_termicas:].all(axis=1)
    return x, ok, ok_contexto


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


def puntos_previos_rejilla(config: ConfiguracionPreventiva) -> int:
    """Puntos de rejilla ANTES de t que usa `evaluar_en_instante`: los de la
    ventana más larga más los `hueco_max_s` previos que necesita la
    segmentación para medir un hueco que toque la ventana (15 + 3 por defecto)."""
    return config.puntos_ventana - 1 + config.hueco_max_s // config.paso_rejilla_s


def alcance_historial_s(config: ConfiguracionPreventiva) -> int:
    """Segundos antes de t cuyas lecturas pueden cambiar el vector de t: la
    rejilla de `evaluar_en_instante` empieza `puntos_previos_rejilla` pasos
    antes de t y su primer punto promedia los `agregacion_s` anteriores.
    15 min + 180 s + 90 s = 1170 s con la configuración por defecto. Una
    lectura más antigua no cambia nada; una que falte dentro de este alcance
    sí (HU-17: el servicio pide el historial por tiempo, no por cantidad)."""
    return puntos_previos_rejilla(config) * config.paso_rejilla_s + config.agregacion_s


class EvaluacionInstante(NamedTuple):
    """Resultado de `evaluar_en_instante`: vector (None si no se predice),
    calidad (CALIDAD_OK / CALIDAD_HISTORIAL_INSUFICIENTE) y, sin vector, el
    motivo estable (MOTIVO_*)."""

    vector: np.ndarray | None
    calidad: str
    motivo: str | None


def _ventana_del_segmento(rejilla: pd.DataFrame, config: ConfiguracionPreventiva) -> np.ndarray:
    """Máscara de los últimos `puntos_ventana` puntos que pertenecen al
    segmento de t en el ENTRENAMIENTO. `calidad.segmentar` corta la serie donde
    hay más de `hueco_max_s` sin temperatura interna y quita esos puntos; las
    ventanas de entrenamiento se rellenan con NaN antes del inicio del segmento
    (`matriz_ventanas`). Aquí se aplica la misma función a la rejilla anclada en
    t, que empieza `hueco_max_s` antes de la ventana para poder medir el hueco
    que la toca. Sin esto, la puerta o el SHT31 de un punto sin temperatura
    interna (DS18B20 en falla) de antes del corte entrarían en la ventana."""
    k = config.puntos_ventana
    g = rejilla["g_ns"].to_numpy()
    segmentos = segmentar(rejilla, config)
    if segmentos.empty or int(segmentos["g_ns"].iloc[-1]) != int(g[-1]):
        # Sin temperatura interna en t no hay segmento: la fila no es ok.
        return np.zeros(k, dtype=bool)
    del_ultimo = segmentos["segmento"].to_numpy() == segmentos["segmento"].iloc[-1]
    inicio = int(segmentos["g_ns"].to_numpy()[del_ultimo][0])
    return g[-k:] >= inicio


def evaluar_en_instante(
    ts_ns: np.ndarray,
    temperaturas: np.ndarray,
    puertas: np.ndarray | None,
    t_eval_ns: int,
    config: ConfiguracionPreventiva,
    conjunto: str = CONJUNTO_TERMICO,
    ambiente: np.ndarray | None = None,
    humedad: np.ndarray | None = None,
) -> EvaluacionInstante:
    """Vector de features en el instante t_eval a partir de lecturas crudas
    (cualquier frecuencia), con la rejilla, la media causal y la segmentación
    del entrenamiento (`calidad.preparar_rejillas`). Las lecturas sin
    temperatura interna válida (NaN) se admiten: no aportan temperatura, pero
    su puerta y su SHT31 cuentan, como en el entrenamiento. `ambiente` y
    `humedad` son la temperatura ambiental y la humedad del SHT31 de cada
    lectura (NaN donde no hay). Con historial insuficiente devuelve vector
    None y el motivo: nunca rellena."""
    paso_ns = config.paso_rejilla_s * 1_000_000_000
    k = config.puntos_ventana
    temperaturas = np.asarray(temperaturas, dtype=float).copy()
    temperaturas[(temperaturas < TEMPERATURA_MIN_FISICA_C) | (temperaturas > TEMPERATURA_MAX_FISICA_C)] = np.nan
    ts_ns = np.asarray(ts_ns, dtype=np.int64)
    pasado = ts_ns <= t_eval_ns
    extras = {
        _COLUMNA_CONTEXTO[canal]: np.asarray(valores, dtype=float)[pasado]
        for canal, valores in ((CANAL_AMBIENTE, ambiente), (CANAL_HUMEDAD, humedad))
        if valores is not None
    }
    rejilla = remuestrear(
        ts_ns[pasado],
        temperaturas[pasado],
        None if puertas is None else np.asarray(puertas, dtype=float)[pasado],
        config.paso_rejilla_s,
        config.agregacion_s,
        ancla_ns=t_eval_ns,
        desde_ns=t_eval_ns - puntos_previos_rejilla(config) * paso_ns,
        hasta_ns=t_eval_ns,
        extras=extras,
    )
    en_segmento = _ventana_del_segmento(rejilla, config)

    def _ventana(columna: str) -> np.ndarray | None:
        if columna not in rejilla.columns:
            return None
        return np.where(en_segmento, rejilla[columna].to_numpy()[-k:], np.nan)[None, :]

    x, ok_termico, ok_contexto = _calcular_matriz(
        _ventana("temperatura"),
        _ventana("puerta"),
        config,
        conjunto,
        _ventana(_COLUMNA_CONTEXTO[CANAL_AMBIENTE]),
        _ventana(_COLUMNA_CONTEXTO[CANAL_HUMEDAD]),
    )
    if not ok_termico[0]:
        return EvaluacionInstante(None, CALIDAD_HISTORIAL_INSUFICIENTE, MOTIVO_SIN_COBERTURA)
    if not ok_contexto[0]:
        return EvaluacionInstante(
            None, CALIDAD_HISTORIAL_INSUFICIENTE, _MOTIVO_SIN_CONTEXTO.get(conjunto, MOTIVO_SIN_COBERTURA)
        )
    return EvaluacionInstante(x[0], CALIDAD_OK, None)


def features_en_instante(
    ts_ns: np.ndarray,
    temperaturas: np.ndarray,
    puertas: np.ndarray | None,
    t_eval_ns: int,
    config: ConfiguracionPreventiva,
    conjunto: str = CONJUNTO_TERMICO,
    ambiente: np.ndarray | None = None,
    humedad: np.ndarray | None = None,
) -> tuple[np.ndarray | None, str]:
    """(vector, calidad) de `evaluar_en_instante`. Con historial insuficiente
    devuelve (None, HISTORIAL_INSUFICIENTE): nunca rellena."""
    vector, calidad, _ = evaluar_en_instante(ts_ns, temperaturas, puertas, t_eval_ns, config, conjunto, ambiente, humedad)
    return vector, calidad
