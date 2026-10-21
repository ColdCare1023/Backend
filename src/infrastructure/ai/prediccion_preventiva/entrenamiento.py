"""Construcción de observaciones, baselines y entrenamiento del Random Forest.

Baselines (todos usan SOLO información disponible en t):
1. clase_mayoritaria  — DummyClassifier(most_frequent).
2. proximidad_limite  — alarma si la distancia al límite más cercano es ≤ m;
                        m se elige en validación.
3. tendencia_reciente — extrapola la pendiente de 10 min a t + h y alarma si
                        cruza el rango; h y el umbral se eligen en validación.
4. regla_legada       — `reglas_riesgo.clasificar_por_regla`, la regla fija que
                        hoy etiqueta el modelo sintético de ColdCare.
"""

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score

from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.ai.features import FeaturesRiesgoTermico
from src.infrastructure.ai.prediccion_preventiva.configuracion import ConfiguracionPreventiva
from src.infrastructure.ai.prediccion_preventiva.etiquetas import ESTADOS_ETIQUETABLES
from src.infrastructure.ai.prediccion_preventiva.evaluacion import elegir_umbral_f1
from src.infrastructure.ai.prediccion_preventiva.features import (
    CONJUNTO_TERMICO,
    features_de_rejillas,
    matriz_ventanas,
    nombres_features,
)
from src.infrastructure.ai.reglas_riesgo import clasificar_por_regla

SEMILLA = 42

# Rejilla de búsqueda deliberadamente pequeña: con pocos incidentes
# independientes, una búsqueda extensa solo sobreajusta la validación.
# Hojas grandes (min_samples_leaf) y submuestreo (max_samples) reducen el
# peso de minutos consecutivos casi idénticos dentro de cada árbol.
REJILLA_HIPERPARAMETROS: dict[str, list] = {
    "max_depth": [4, 8, None],
    "min_samples_leaf": [10, 50],
    "max_features": ["sqrt", 0.5],
    "class_weight": [None, "balanced_subsample"],
}
HIPERPARAMETROS_FIJOS = {"n_estimators": 300, "max_samples": 0.5, "bootstrap": True}
HORIZONTES_TENDENCIA_MIN = (5, 10, 15, 20, 30)


@dataclass(slots=True)
class Observaciones:
    tabla: pd.DataFrame  # filas etiquetables con features válidas
    x: np.ndarray
    nombres: list[str]
    excluidas: dict = field(default_factory=dict)

    @property
    def y(self) -> np.ndarray:
        return self.tabla["y"].to_numpy(dtype=int)


def construir_observaciones(
    etiquetadas: pd.DataFrame, config: ConfiguracionPreventiva, conjunto: str = CONJUNTO_TERMICO
) -> Observaciones:
    x_todo, ok = features_de_rejillas(etiquetadas, config, conjunto)
    etiquetable = etiquetadas["estado_etiqueta"].isin(ESTADOS_ETIQUETABLES).to_numpy()
    excluidas = etiquetadas.loc[~etiquetable, "estado_etiqueta"].value_counts().to_dict()
    excluidas["historial_insuficiente"] = int((etiquetable & ~ok).sum())
    mascara = etiquetable & ok
    tabla = etiquetadas[mascara].reset_index(drop=True)
    # Bloque de dependencia para remuestreo: entorno x día local del recinto.
    tabla["bloque"] = tabla["entorno_id"] + "|" + tabla["timestamp"].dt.strftime("%Y-%m-%d")
    return Observaciones(tabla=tabla, x=x_todo[mascara], nombres=nombres_features(config, conjunto), excluidas=excluidas)


# ── Baselines ────────────────────────────────────────────────────


def _col(obs: Observaciones, nombre: str) -> np.ndarray:
    return obs.x[:, obs.nombres.index(nombre)]


def puntuacion_proximidad(obs: Observaciones) -> np.ndarray:
    """Mayor cuanto más cerca del límite (negativo de la distancia en °C)."""
    return -np.minimum(_col(obs, "dist_limite_sup"), _col(obs, "dist_limite_inf"))


def puntuacion_tendencia(obs: Observaciones, horizonte_min: float, config: ConfiguracionPreventiva) -> np.ndarray:
    """Exceso (°C) de la temperatura extrapolada a t + h sobre el rango."""
    pendiente = _col(obs, "pendiente_10m") if "pendiente_10m" in obs.nombres else _col(obs, f"pendiente_{config.ventana_max_min}m")
    proyectada = _col(obs, "temp_actual") + pendiente * horizonte_min
    return np.maximum(proyectada - config.rango.maximo_celsius, config.rango.minimo_celsius - proyectada)


def puntuacion_regla_legada(etiquetadas: pd.DataFrame, obs: Observaciones, config: ConfiguracionPreventiva) -> np.ndarray:
    """Aplica la regla legada sobre los 20 puntos previos de la rejilla (el
    backend usa las 20 lecturas previas). Solo usa temperatura interna:
    la regla no lee temperatura ambiental, humedad ni puerta."""
    previos = 20
    rango = config.rango
    resultado = np.zeros(len(obs.tabla))
    clave = obs.tabla.set_index(["segmento_id", "g_ns"]).index
    posicion = pd.Series(np.arange(len(obs.tabla)), index=clave)
    for segmento_id, seg in etiquetadas.groupby("segmento_id", sort=False):
        seg = seg.sort_values("g_ns")
        ventanas = matriz_ventanas(seg["temperatura"].to_numpy(), previos + 1)
        g = seg["g_ns"].to_numpy()
        for i, gi in enumerate(g):
            k = (segmento_id, gi)
            if k not in posicion.index:
                continue
            historia = ventanas[i, :-1]
            validas = historia[np.isfinite(historia)]
            fuera = (validas < rango.minimo_celsius) | (validas > rango.maximo_celsius)
            duracion = 0.0
            for paso, v in enumerate(historia[::-1], start=1):
                if not np.isfinite(v) or rango.contiene(v):
                    break
                duracion = paso * config.paso_rejilla_s / 60
            tendencia = float(np.polyfit(np.arange(validas.size), validas, 1)[0]) if validas.size >= 2 else 0.0
            features = FeaturesRiesgoTermico(
                temperatura_ambiental=0.0,
                humedad_ambiental=0.0,
                temperatura_interna=float(ventanas[i, -1]),
                diferencia_sensores=0.0,
                duracion_fuera_rango=duracion,
                frecuencia_desviaciones=float(fuera.sum()),
                tendencia_termica=tendencia,
                apertura_refrigerador=False,
                hora_evento=0,
                estado_conectividad_online=True,
            )
            resultado[posicion[k]] = float(clasificar_por_regla(features) == NivelRiesgo.RIESGO_PREVENTIVO)
    return resultado


# ── Random Forest ────────────────────────────────────────────────


def crear_modelo(parametros: dict, semilla: int = SEMILLA, n_jobs: int = -1) -> RandomForestClassifier:
    return RandomForestClassifier(**HIPERPARAMETROS_FIJOS, **parametros, random_state=semilla, n_jobs=n_jobs)


def combinaciones_rejilla(rejilla: dict[str, list] = REJILLA_HIPERPARAMETROS) -> list[dict]:
    claves = list(rejilla)
    return [dict(zip(claves, valores, strict=True)) for valores in itertools.product(*(rejilla[c] for c in claves))]


def predicciones_fuera_de_pliegue(x: np.ndarray, y: np.ndarray, pliegues, parametros: dict, semilla: int) -> tuple[np.ndarray, list[dict]]:
    """Puntuaciones de validación concatenadas (cada observación de
    validación predicha por un modelo que solo vio su pasado)."""
    oof = np.full(y.size, np.nan)
    por_pliegue = []
    for pliegue in pliegues:
        tr, va = pliegue.entrenamiento, pliegue.validacion
        info = {"pliegue": pliegue.nombre, "n_entrenamiento": int(tr.size), "n_validacion": int(va.size),
                "positivos_entrenamiento": int(y[tr].sum()), "positivos_validacion": int(y[va].sum())}
        if y[tr].sum() == 0 or va.size == 0:
            info["average_precision"] = None
            info["motivo"] = "pliegue sin positivos de entrenamiento o sin validación"
            por_pliegue.append(info)
            continue
        modelo = crear_modelo(parametros, semilla).fit(x[tr], y[tr])
        # Predicción en un hilo: en paralelo el orden de suma de los árboles
        # varía y cambia el último decimal entre ejecuciones.
        oof[va] = modelo.set_params(n_jobs=1).predict_proba(x[va])[:, 1]
        info["average_precision"] = (
            float(average_precision_score(y[va], oof[va])) if 0 < y[va].sum() < va.size else None
        )
        por_pliegue.append(info)
    return oof, por_pliegue


def buscar_hiperparametros(
    x: np.ndarray, y: np.ndarray, pliegues, semilla: int = SEMILLA, rejilla: dict[str, list] = REJILLA_HIPERPARAMETROS
) -> tuple[dict, list[dict]]:
    """Selecciona por Average Precision de las predicciones fuera de pliegue
    concatenadas (insensible al umbral). Devuelve (mejor, resultados)."""
    resultados = []
    for parametros in combinaciones_rejilla(rejilla):
        oof, por_pliegue = predicciones_fuera_de_pliegue(x, y, pliegues, parametros, semilla)
        valido = np.isfinite(oof)
        ap = float(average_precision_score(y[valido], oof[valido])) if 0 < y[valido].sum() < valido.sum() else None
        resultados.append({"parametros": parametros, "ap_oof": ap, "pliegues": por_pliegue})
    candidatos = [r for r in resultados if r["ap_oof"] is not None]
    if not candidatos:
        raise RuntimeError("Ninguna combinación produjo predicciones de validación con ambas clases")
    mejor = max(candidatos, key=lambda r: r["ap_oof"])
    return mejor["parametros"], resultados


def ajustar_baselines(obs: Observaciones, etiquetadas: pd.DataFrame, indices_validacion: np.ndarray, config: ConfiguracionPreventiva) -> dict:
    """Elige los parámetros libres de los baselines con las MISMAS
    observaciones de validación que el umbral del Random Forest."""
    y = obs.y
    va = indices_validacion
    proximidad = puntuacion_proximidad(obs)
    mejor_tendencia = None
    for h in HORIZONTES_TENDENCIA_MIN:
        p = puntuacion_tendencia(obs, h, config)
        ap = float(average_precision_score(y[va], p[va]))
        if mejor_tendencia is None or ap > mejor_tendencia["ap_validacion"]:
            mejor_tendencia = {"horizonte_extrapolacion_min": h, "ap_validacion": ap}
    tendencia = puntuacion_tendencia(obs, mejor_tendencia["horizonte_extrapolacion_min"], config)
    legada = puntuacion_regla_legada(etiquetadas, obs, config)
    return {
        "proximidad_limite": {"umbral": elegir_umbral_f1(y[va], proximidad[va])},
        "tendencia_reciente": {**mejor_tendencia, "umbral": elegir_umbral_f1(y[va], tendencia[va])},
        "regla_legada": {"umbral": {"umbral": 0.5, "nota": "regla fija: 1 = riesgo_preventivo"}},
        "_puntuaciones": {"proximidad_limite": proximidad, "tendencia_reciente": tendencia, "regla_legada": legada},
    }


def baseline_mayoritario(y_entrenamiento: np.ndarray, n: int) -> np.ndarray:
    modelo = DummyClassifier(strategy="most_frequent").fit(np.zeros((y_entrenamiento.size, 1)), y_entrenamiento)
    return modelo.predict_proba(np.zeros((n, 1)))[:, list(modelo.classes_).index(1)] if 1 in modelo.classes_ else np.zeros(n)


def marca_tiempo() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
