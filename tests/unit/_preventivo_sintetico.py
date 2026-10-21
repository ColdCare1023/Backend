"""Series SINTÉTICAS mínimas para las pruebas del pipeline preventivo.

Solo verifican propiedades del código (causalidad, paridad, etiquetas,
fugas). Nunca se usan ni se reportan como resultados experimentales.
"""

import numpy as np
import pandas as pd

INICIO = pd.Timestamp("2026-01-05 00:00:00", tz="UTC")


def lecturas(
    valores,
    paso_s: float = 60.0,
    serie_id: str = "S1",
    entorno_id: str = "lab",
    inicio: pd.Timestamp = INICIO,
    jitter_s: float = 0.0,
    puerta=None,
    semilla: int = 0,
) -> pd.DataFrame:
    valores = np.asarray(valores, dtype=float)
    rng = np.random.default_rng(semilla)
    offsets = np.arange(valores.size) * paso_s
    if jitter_s:
        offsets = offsets + rng.uniform(-jitter_s, jitter_s, size=valores.size)
    return pd.DataFrame(
        {
            "fuente": "sintetica",
            "entorno_id": entorno_id,
            "serie_id": serie_id,
            "timestamp": inicio + pd.to_timedelta(offsets, unit="s"),
            "temperatura_interna": valores,
            "puerta_abierta": np.nan if puerta is None else np.asarray(puerta, dtype=float),
        }
    )


def por_minuto(segmentos: list[tuple[float, float, int]]) -> np.ndarray:
    """Concatena rampas lineales (inicio_c, fin_c, minutos) muestreadas a 1/min."""
    partes = [np.linspace(a, b, n, endpoint=False) for a, b, n in segmentos]
    return np.concatenate(partes)
