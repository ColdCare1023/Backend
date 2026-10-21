"""Detección de excursiones y agrupación en eventos independientes.

- Excursión (punto): temperatura válida de la rejilla fuera de 2-8 °C, la
  misma regla determinista que `LecturaTermica.es_excursion_confirmada`.
- Episodio: tramos fuera de rango de UNA serie separados por menos de
  `fusion_episodios_min` se funden. Una oscilación alrededor de 8 °C es un
  solo episodio, no decenas.
- Incidente: episodios de distintas series del MISMO entorno físico que se
  solapan en el tiempo. Nueve sensores en una misma cámara que suben a la
  vez por una apertura de puerta son UN incidente, no nueve eventos
  independientes. Los incidentes son la unidad de conteo, de partición y
  de remuestreo para los intervalos de incertidumbre.
"""

import numpy as np
import pandas as pd

from src.infrastructure.ai.prediccion_preventiva.configuracion import ConfiguracionPreventiva

NS_POR_MIN = 60 * 1_000_000_000


def detectar_episodios(rejillas: pd.DataFrame, config: ConfiguracionPreventiva) -> pd.DataFrame:
    """Un renglón por episodio con inicio/fin (punto de rejilla), tipo,
    pico y duración. Nunca cruza segmentos."""
    rango = config.rango
    fusion_ns = config.fusion_episodios_min * NS_POR_MIN
    paso_ns = config.paso_rejilla_s * 1_000_000_000
    filas = []
    for (fuente, entorno, serie, segmento), seg in rejillas.groupby(
        ["fuente", "entorno_id", "serie_id", "segmento_id"], sort=True
    ):
        t = seg["temperatura"].to_numpy()
        g = seg["g_ns"].to_numpy()
        fuera = np.isfinite(t) & ((t < rango.minimo_celsius) | (t > rango.maximo_celsius))
        idx = np.flatnonzero(fuera)
        if idx.size == 0:
            continue
        grupos: list[list[int]] = [[idx[0]]]
        for i in idx[1:]:
            if g[i] - g[grupos[-1][-1]] <= fusion_ns:
                grupos[-1].append(i)
            else:
                grupos.append([i])
        # Primer punto con dato del segmento: si ya está fuera de rango, la
        # SALIDA de rango no se observó (el sensor se instaló caliente o la
        # serie reanuda tras un corte). Se conserva el episodio, marcado.
        primer_valido = int(np.flatnonzero(np.isfinite(t))[0])
        for miembros in grupos:
            a, b = miembros[0], miembros[-1]
            tramo = t[a : b + 1]
            fuera_tramo = t[miembros]
            alta = bool((fuera_tramo > rango.maximo_celsius).any())
            baja = bool((fuera_tramo < rango.minimo_celsius).any())
            filas.append(
                {
                    "fuente": fuente,
                    "entorno_id": entorno,
                    "serie_id": serie,
                    "segmento_id": segmento,
                    "inicio_ns": int(g[a]),
                    "fin_ns": int(g[b]),
                    "duracion_min": (int(g[b]) - int(g[a]) + paso_ns) / NS_POR_MIN,
                    "minutos_fuera": len(miembros) * config.paso_rejilla_s / 60,
                    "tipo": "mixta" if alta and baja else ("alta" if alta else "baja"),
                    "pico_c": float(np.nanmax(tramo)) if alta else float(np.nanmin(tramo)),
                    "inicio_observado": a != primer_valido,
                }
            )
    columnas = [
        "fuente", "entorno_id", "serie_id", "segmento_id", "inicio_ns", "fin_ns",
        "duracion_min", "minutos_fuera", "tipo", "pico_c", "inicio_observado",
    ]
    episodios = pd.DataFrame(filas, columns=columnas)
    episodios["episodio_id"] = [f"{s}@{i}" for s, i in zip(episodios["serie_id"], episodios["inicio_ns"], strict=True)]
    return episodios


def agrupar_incidentes(episodios: pd.DataFrame, config: ConfiguracionPreventiva) -> pd.DataFrame:
    """Asigna `incidente_id`: unión de episodios del mismo entorno cuyos
    intervalos [inicio, fin] (ampliados por la tolerancia de fusión) se
    solapan."""
    if episodios.empty:
        return episodios.assign(incidente_id=pd.Series(dtype=str))
    tolerancia = config.fusion_episodios_min * NS_POR_MIN
    salida = episodios.copy()
    salida["incidente_id"] = ""
    for entorno, grupo in salida.groupby("entorno_id", sort=True):
        orden = grupo.sort_values("inicio_ns")
        actual_fin = None
        contador = -1
        for indice, fila in orden.iterrows():
            if actual_fin is None or fila["inicio_ns"] > actual_fin + tolerancia:
                contador += 1
                actual_fin = fila["fin_ns"]
            else:
                actual_fin = max(actual_fin, fila["fin_ns"])
            salida.at[indice, "incidente_id"] = f"{entorno}#{contador}"
    return salida


def resumen_incidentes(episodios: pd.DataFrame) -> pd.DataFrame:
    """Un renglón por incidente. `inicio_observado` es falso cuando ninguno
    de sus episodios tiene una salida de rango observada (solo arranques en
    caliente): ese incidente no cuenta como excursión independiente."""
    columnas = ["incidente_id", "entorno_id", "inicio_ns", "fin_ns", "series", "tipo", "pico_c", "inicio_observado"]
    if episodios.empty:
        return pd.DataFrame(columns=columnas)
    return (
        episodios.groupby("incidente_id")
        .agg(
            entorno_id=("entorno_id", "first"),
            inicio_ns=("inicio_ns", "min"),
            fin_ns=("fin_ns", "max"),
            series=("serie_id", "nunique"),
            tipo=("tipo", lambda s: "mixta" if s.nunique() > 1 else s.iloc[0]),
            pico_c=("pico_c", "max"),
            inicio_observado=("inicio_observado", "any"),
        )
        .reset_index()
        .sort_values("inicio_ns")[columnas]
    )
