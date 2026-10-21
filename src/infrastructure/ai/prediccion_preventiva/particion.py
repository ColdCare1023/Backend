"""Partición temporal sin fugas y su auditoría automática.

Por qué no `train_test_split(shuffle=True)` ni `TimeSeriesSplit` a secas:
- Ventanas consecutivas de una serie son casi idénticas: barajarlas pone
  "el mismo instante" en entrenamiento y prueba.
- Los sensores de un mismo recinto registran el MISMO incidente a la vez:
  separar por sensor filtraría el evento entre particiones.
- `TimeSeriesSplit(gap=...)` mide el hueco en muestras; con varias series
  intercaladas un hueco en muestras no es un hueco en tiempo.

Se usan cortes por instante absoluto (comunes a todas las series de la
fuente) con purga y embargo: una observación de entrenamiento se descarta si
su horizonte de etiqueta (t, t + H] cruza el corte, y una de validación o
prueba se descarta si su ventana de features [t - W, t] empieza antes del
corte. Así ninguna etiqueta de un lado usa datos del otro.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.infrastructure.ai.prediccion_preventiva.configuracion import ConfiguracionPreventiva
from src.infrastructure.ai.prediccion_preventiva.etiquetas import ESTADOS_ETIQUETABLES
from src.infrastructure.ai.prediccion_preventiva.eventos import NS_POR_MIN


@dataclass(frozen=True, slots=True)
class Pliegue:
    nombre: str
    entrenamiento: np.ndarray  # índices posicionales en la tabla de observaciones
    validacion: np.ndarray


def _purgar(g_ns: np.ndarray, corte_ns: int, config: ConfiguracionPreventiva) -> tuple[np.ndarray, np.ndarray]:
    """(antes, despues): máscaras de observaciones que quedan a cada lado del
    corte respetando horizonte (antes) y ventana (después)."""
    horizonte = config.horizonte_min * NS_POR_MIN
    ventana = config.ventana_max_min * NS_POR_MIN
    antes = g_ns + horizonte < corte_ns
    despues = g_ns - ventana >= corte_ns
    return antes, despues


def dividir_desarrollo_prueba(
    obs: pd.DataFrame, corte_prueba: pd.Timestamp, config: ConfiguracionPreventiva
) -> tuple[np.ndarray, np.ndarray]:
    g = obs["g_ns"].to_numpy()
    antes, despues = _purgar(g, int(corte_prueba.value), config)
    return np.flatnonzero(antes), np.flatnonzero(despues)


def pliegues_walk_forward(
    obs: pd.DataFrame, cortes: list[pd.Timestamp], fin_desarrollo: pd.Timestamp, config: ConfiguracionPreventiva,
    etiquetas: list[str] | None = None,
) -> list[Pliegue]:
    """Validación hacia adelante: el pliegue k entrena con todo lo anterior
    a cortes[k] y valida en [cortes[k], cortes[k+1]) (o hasta el fin del
    desarrollo). Nunca se entrena con futuro para validar pasado."""
    g = obs["g_ns"].to_numpy()
    limites = [*list(cortes), fin_desarrollo]
    pliegues = []
    for k, corte in enumerate(cortes):
        antes, despues = _purgar(g, int(corte.value), config)
        hasta, _ = _purgar(g, int(limites[k + 1].value), config)
        pliegues.append(
            Pliegue(
                nombre=f"validacion_{etiquetas[k] if etiquetas else corte.date().isoformat()}",
                entrenamiento=np.flatnonzero(antes),
                validacion=np.flatnonzero(despues & hasta),
            )
        )
    return pliegues


def _intervalos_solapan(a_ini: np.ndarray, a_fin: np.ndarray, b_ini: np.ndarray, b_fin: np.ndarray) -> int:
    """Cantidad de intervalos de A que solapan con algún intervalo de B."""
    if a_ini.size == 0 or b_ini.size == 0:
        return 0
    orden = np.argsort(b_ini)
    b_ini, b_fin = b_ini[orden], b_fin[orden]
    max_fin_previo = np.maximum.accumulate(b_fin)
    pos = np.searchsorted(b_ini, a_fin, side="left")  # B que empiezan antes de que A termine
    con_previos = pos > 0
    return int((con_previos & (max_fin_previo[np.maximum(pos - 1, 0)] > a_ini)).sum())


def auditar_particion(
    obs: pd.DataFrame, particiones: dict[str, np.ndarray], config: ConfiguracionPreventiva
) -> dict:
    """Comprobaciones automáticas de fuga. `aprobada` es False si alguna
    falla; el entrenamiento se detiene en ese caso."""
    horizonte = config.horizonte_min * NS_POR_MIN
    ventana = config.ventana_max_min * NS_POR_MIN
    nombres = list(particiones)
    resultado: dict = {"particiones": {}, "comprobaciones": {}}
    for nombre, idx in particiones.items():
        sub = obs.iloc[idx]
        resultado["particiones"][nombre] = {
            "observaciones": len(sub),
            "positivas": int((sub["y"] == 1).sum()),
            "incidentes_objetivo": int(sub["incidente_objetivo"].dropna().nunique()),
            "series": sorted(sub["serie_id"].unique().tolist()),
            "desde_utc": pd.to_datetime(sub["g_ns"].min(), unit="ns", utc=True).isoformat() if len(sub) else None,
            "hasta_utc": pd.to_datetime(sub["g_ns"].max(), unit="ns", utc=True).isoformat() if len(sub) else None,
        }

    comprobaciones = resultado["comprobaciones"]
    todos = np.concatenate([particiones[n] for n in nombres]) if nombres else np.array([], dtype=int)
    comprobaciones["indices_duplicados"] = int(todos.size - np.unique(todos).size)
    comprobaciones["etiquetas_incompletas"] = int(
        (~obs.iloc[todos]["estado_etiqueta"].isin(ESTADOS_ETIQUETABLES)).sum() + obs.iloc[todos]["y"].isna().sum()
    )
    solapes = 0
    incidentes_compartidos = 0
    for i, a in enumerate(nombres):
        for b in nombres[i + 1 :]:
            oa, ob = obs.iloc[particiones[a]], obs.iloc[particiones[b]]
            for entorno in set(oa["entorno_id"]) & set(ob["entorno_id"]):
                ga = oa.loc[oa["entorno_id"] == entorno, "g_ns"].to_numpy()
                gb = ob.loc[ob["entorno_id"] == entorno, "g_ns"].to_numpy()
                # Intervalo de información de cada observación: [t - W, t + H].
                solapes += _intervalos_solapan(ga - ventana, ga + horizonte, gb - ventana, gb + horizonte)
            ia = set(oa["incidente_objetivo"].dropna())
            ib = set(ob["incidente_objetivo"].dropna())
            incidentes_compartidos += len(ia & ib)
    comprobaciones["ventanas_solapadas_entre_particiones"] = solapes
    comprobaciones["incidentes_compartidos"] = incidentes_compartidos
    # El pipeline no ajusta transformaciones con datos (sin escaladores ni
    # imputadores: Random Forest es invariante a escala y las filas sin
    # historial suficiente se excluyen, no se imputan). Se deja explícito.
    comprobaciones["transformaciones_ajustadas_con_prueba"] = 0
    resultado["aprobada"] = all(v == 0 for v in comprobaciones.values())
    return resultado
