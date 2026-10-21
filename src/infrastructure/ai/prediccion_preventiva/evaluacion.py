"""Métricas por observación y por evento, con incertidumbre honesta.

- Una métrica que no puede calcularse (p. ej. ROC-AUC sin positivos) se
  informa como `None` con su motivo, nunca como 0.
- Los intervalos respetan la dependencia: los de sensibilidad por incidente
  son binomiales exactos sobre incidentes (la unidad independiente); los de
  métricas por observación se obtienen remuestreando bloques (entorno x día)
  y solo si hay bloques suficientes. Nunca se trata cada minuto como una
  observación independiente.
"""

from collections.abc import Callable

import numpy as np
import pandas as pd
from scipy.stats import binomtest
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    brier_score_loss,
    confusion_matrix,
    precision_recall_curve,
    precision_recall_fscore_support,
    roc_auc_score,
)

from src.infrastructure.ai.prediccion_preventiva.configuracion import ConfiguracionPreventiva
from src.infrastructure.ai.prediccion_preventiva.eventos import NS_POR_MIN

NO_ESTIMABLE = "no estimable"
MIN_BLOQUES_BOOTSTRAP = 5
N_BOOTSTRAP = 2000


def _no_estimable(motivo: str) -> dict:
    return {"valor": None, "estado": NO_ESTIMABLE, "motivo": motivo}


def elegir_umbral_f1(y: np.ndarray, puntuacion: np.ndarray) -> dict:
    """Umbral que maximiza F1 de la clase positiva. Se aplica SOLO a
    predicciones de validación; jamás a la prueba."""
    if (y == 1).sum() == 0 or (y == 0).sum() == 0:
        return {"umbral": None, "f1": None, "motivo": "validación sin ambas clases"}
    precision, recall, umbrales = precision_recall_curve(y, puntuacion)
    with np.errstate(invalid="ignore", divide="ignore"):
        f1 = np.where(precision + recall > 0, 2 * precision * recall / (precision + recall), 0.0)[:-1]
    mejor = int(np.nanargmax(f1))
    return {
        "umbral": float(umbrales[mejor]),
        "f1": float(f1[mejor]),
        "precision": float(precision[mejor]),
        "recall": float(recall[mejor]),
    }


def metricas_observacion(y: np.ndarray, puntuacion: np.ndarray, umbral: float, es_probabilidad: bool) -> dict:
    y = np.asarray(y, dtype=int)
    pred = (puntuacion >= umbral).astype(int)
    n_pos, n_neg = int((y == 1).sum()), int((y == 0).sum())
    res: dict = {"n": int(y.size), "positivos": n_pos, "negativos": n_neg, "umbral": float(umbral)}
    res["accuracy"] = float(accuracy_score(y, pred)) if y.size else None
    p, r, f, s = precision_recall_fscore_support(y, pred, labels=[0, 1], zero_division=np.nan)
    res["por_clase"] = {
        nombre: {
            "precision": None if np.isnan(p[i]) else float(p[i]),
            "recall": None if np.isnan(r[i]) else float(r[i]),
            "f1": None if np.isnan(f[i]) else float(f[i]),
            "soporte": int(s[i]),
        }
        for i, nombre in enumerate(("normal", "riesgo_preventivo"))
    }
    f1s = [v["f1"] for v in res["por_clase"].values() if v["f1"] is not None]
    res["macro_f1"] = float(np.mean(f1s)) if len(f1s) == 2 else None
    res["matriz_confusion"] = {
        "etiquetas": ["normal", "riesgo_preventivo"],
        "filas_real_columnas_predicho": confusion_matrix(y, pred, labels=[0, 1]).tolist(),
    }
    if n_pos and n_neg:
        res["balanced_accuracy"] = float(balanced_accuracy_score(y, pred))
        res["average_precision"] = float(average_precision_score(y, puntuacion))
        res["roc_auc"] = float(roc_auc_score(y, puntuacion))
        res["prevalencia"] = n_pos / y.size
    else:
        motivo = "la partición no contiene ambas clases"
        res["balanced_accuracy"] = _no_estimable(motivo)
        res["average_precision"] = _no_estimable(motivo)
        res["roc_auc"] = _no_estimable(motivo)
        res["prevalencia"] = n_pos / y.size if y.size else None
    res["brier"] = (
        float(brier_score_loss(y, np.clip(puntuacion, 0, 1)))
        if es_probabilidad and y.size
        else _no_estimable("la puntuación no es una probabilidad")
    )
    return res


def curva_pr(y: np.ndarray, puntuacion: np.ndarray, puntos: int = 200) -> dict | None:
    if (y == 1).sum() == 0 or (y == 0).sum() == 0:
        return None
    precision, recall, _ = precision_recall_curve(y, puntuacion)
    indice = np.unique(np.linspace(0, precision.size - 1, min(puntos, precision.size)).astype(int))
    return {"precision": precision[indice].round(5).tolist(), "recall": recall[indice].round(5).tolist()}


def episodios_de_alarma(obs: pd.DataFrame, alarma: np.ndarray, config: ConfiguracionPreventiva) -> pd.DataFrame:
    """Agrupa instantes de alarma consecutivos (por serie, separados por menos
    de la tolerancia de fusión) en episodios de alarma. Un episodio de alarma
    es FALSO si ninguno de sus instantes precede a una excursión dentro del
    horizonte (todos con y = 0)."""
    tolerancia = config.fusion_episodios_min * NS_POR_MIN
    filas = []
    sub = obs.assign(_alarma=alarma)
    for serie, grupo in sub[sub["_alarma"]].groupby("serie_id", sort=True):
        grupo = grupo.sort_values("g_ns")
        g = grupo["g_ns"].to_numpy()
        y = grupo["y"].to_numpy()
        inicio = 0
        for i in range(1, g.size + 1):
            if i == g.size or g[i] - g[i - 1] > tolerancia:
                filas.append(
                    {
                        "serie_id": serie,
                        "inicio_ns": int(g[inicio]),
                        "fin_ns": int(g[i - 1]),
                        "instantes": i - inicio,
                        "verdadero": bool((y[inicio:i] == 1).any()),
                    }
                )
                inicio = i
    return pd.DataFrame(filas, columns=["serie_id", "inicio_ns", "fin_ns", "instantes", "verdadero"])


def metricas_evento(
    obs: pd.DataFrame,
    alarma: np.ndarray,
    episodios: pd.DataFrame,
    config: ConfiguracionPreventiva,
) -> dict:
    """`obs` son las observaciones evaluadas (con features válidas). Un
    episodio es EVALUABLE si tiene al menos una observación elegible en su
    ventana previa (t ∈ [inicio - H, inicio)); si no, el modelo nunca tuvo
    oportunidad de anticiparlo y no cuenta ni a favor ni en contra."""
    if "inicio_observado" in episodios:
        # Una salida de rango no observada (arranque en caliente) no es un
        # evento que pudiera anticiparse.
        episodios = episodios[episodios["inicio_observado"].astype(bool)]
    sub = obs.assign(_alarma=alarma)
    previas = sub[sub["y"] == 1]
    detalle = []
    for _, ep in episodios.iterrows():
        cand = previas[previas["episodio_objetivo"] == ep["episodio_id"]]
        if cand.empty:
            detalle.append({**ep.to_dict(), "evaluable": False, "anticipado": None, "anticipacion_min": None})
            continue
        alarmas = cand[cand["_alarma"]]
        anticipado = not alarmas.empty
        detalle.append(
            {
                **ep.to_dict(),
                "evaluable": True,
                "anticipado": anticipado,
                "anticipacion_min": float((ep["inicio_ns"] - alarmas["g_ns"].min()) / NS_POR_MIN) if anticipado else None,
                "alarmas_previas": len(alarmas),
                "oportunidad_min": float((ep["inicio_ns"] - cand["g_ns"].min()) / NS_POR_MIN),
            }
        )
    det = pd.DataFrame(detalle)
    res: dict = {"episodios_total": len(episodios)}
    evaluables = det[det["evaluable"]] if not det.empty else det
    res["episodios_evaluables"] = len(evaluables)
    res["episodios_anticipados"] = int(evaluables["anticipado"].sum()) if len(evaluables) else 0
    res["episodios_no_anticipados"] = res["episodios_evaluables"] - res["episodios_anticipados"]
    res["sensibilidad_por_episodio"] = (
        res["episodios_anticipados"] / res["episodios_evaluables"]
        if res["episodios_evaluables"]
        else _no_estimable("sin episodios evaluables")
    )

    # Incidentes (unidad independiente): anticipado si alguno de sus
    # episodios evaluables fue anticipado.
    if len(evaluables):
        por_incidente = evaluables.groupby("incidente_id")["anticipado"].any()
        k, n = int(por_incidente.sum()), int(por_incidente.size)
        res["incidentes_evaluables"] = n
        res["incidentes_anticipados"] = k
        ic = binomtest(k, n).proportion_ci(confidence_level=0.95, method="exact")
        res["sensibilidad_por_incidente"] = {
            "valor": k / n,
            "ic95_exacto": [float(ic.low), float(ic.high)],
            "nota": "Clopper–Pearson sobre incidentes independientes",
        }
    else:
        res["incidentes_evaluables"] = 0
        res["incidentes_anticipados"] = 0
        res["sensibilidad_por_incidente"] = _no_estimable("sin incidentes evaluables")

    anticip = evaluables["anticipacion_min"].dropna() if len(evaluables) else pd.Series(dtype=float)
    # "Anticipado" con UNA alarma en los 30 min previos es una definición
    # laxa: una alarma 1 min antes cuenta. Se informa además la fracción de
    # episodios evaluables anticipados con al menos 5 y 10 min de margen.
    for minimo in (5, 10):
        res[f"sensibilidad_por_episodio_min_{minimo}min"] = (
            float((anticip >= minimo).sum() / res["episodios_evaluables"])
            if res["episodios_evaluables"]
            else _no_estimable("sin episodios evaluables")
        )
    res["anticipacion_min"] = (
        {
            "n": int(anticip.size),
            "mediana": float(anticip.median()),
            "p25": float(anticip.quantile(0.25)),
            "p75": float(anticip.quantile(0.75)),
            "min": float(anticip.min()),
            "max": float(anticip.max()),
            "valores": anticip.round(2).tolist(),
        }
        if anticip.size
        else _no_estimable("ningún episodio anticipado")
    )
    if len(evaluables):
        res["por_tipo"] = {
            tipo: {
                "evaluables": len(g),
                "anticipados": int(g["anticipado"].sum()),
            }
            for tipo, g in evaluables.groupby("tipo")
        }
        res["alarmas_por_episodio_anticipado"] = (
            float(evaluables.loc[evaluables["anticipado"].astype(bool), "alarmas_previas"].mean())
            if res["episodios_anticipados"]
            else None
        )
    alarmas_ep = episodios_de_alarma(obs, alarma, config)
    dias_serie = len(obs) * config.paso_rejilla_s / 86400.0
    falsas = int((~alarmas_ep["verdadero"]).sum()) if len(alarmas_ep) else 0
    res["episodios_alarma"] = len(alarmas_ep)
    res["falsas_alarmas"] = falsas
    res["dias_sensor_observados"] = round(dias_serie, 3)
    res["falsas_alarmas_por_dia_sensor"] = falsas / dias_serie if dias_serie else _no_estimable("sin tiempo observado")
    res["detalle_episodios"] = det.drop(columns=[c for c in ("fuente",) if c in det.columns]).to_dict(orient="records")
    return res


def bootstrap_por_bloques(
    obs: pd.DataFrame,
    calcular: Callable[[pd.DataFrame], float | None],
    semilla: int,
    n: int = N_BOOTSTRAP,
) -> dict:
    """IC95 percentil remuestreando bloques entorno x día local. Con menos de
    MIN_BLOQUES_BOOTSTRAP bloques se declara no estimable."""
    bloques = obs["bloque"].to_numpy()
    unicos = np.unique(bloques)
    if unicos.size < MIN_BLOQUES_BOOTSTRAP:
        return _no_estimable(f"solo {unicos.size} bloques independientes (mínimo {MIN_BLOQUES_BOOTSTRAP})")
    rng = np.random.default_rng(semilla)
    posiciones = {b: np.flatnonzero(bloques == b) for b in unicos}
    valores = []
    for _ in range(n):
        elegidos = rng.choice(unicos, size=unicos.size, replace=True)
        idx = np.concatenate([posiciones[b] for b in elegidos])
        v = calcular(obs.iloc[idx])
        if v is not None and np.isfinite(v):
            valores.append(v)
    if len(valores) < n * 0.5:
        return _no_estimable("la métrica no es calculable en la mayoría de remuestreos")
    return {"ic95_percentil": [float(np.percentile(valores, 2.5)), float(np.percentile(valores, 97.5))], "bloques": int(unicos.size)}
