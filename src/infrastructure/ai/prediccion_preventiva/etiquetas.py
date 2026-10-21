"""Etiquetado preventivo binario: normal (0) / riesgo_preventivo (1).

Para un punto de rejilla t de un segmento continuo, con H = horizonte:

    fuera_de_rango   T(t) válida y fuera de 2-8 °C → excursión ACTUAL: la
                     decide la regla determinista; el clasificador no la ve.
    en_episodio      T(t) en rango pero dentro de un episodio fundido (una
                     oscilación de vuelta al rango en mitad de la excursión):
                     tampoco es un instante "preventivo".
    sin_dato         sin lectura válida en el punto t.
    positivo (y=1)   existe un inicio de episodio s con t < s ≤ t + H.
    negativo (y=0)   no hay inicio en (t, t + H], t + H no rebasa el final
                     del segmento y la cobertura válida de (t, t + H] es
                     ≥ cobertura_min_horizonte.
    no_etiquetable   ni lo uno ni lo otro: falta observación futura
                     suficiente para afirmar que NO ocurrió una excursión.

La etiqueta usa información posterior a t SOLO porque se construye sobre
historia ya registrada; el extractor de features (features.py) nunca lee
más allá de t.
"""

import numpy as np
import pandas as pd

from src.infrastructure.ai.prediccion_preventiva.configuracion import ConfiguracionPreventiva
from src.infrastructure.ai.prediccion_preventiva.eventos import NS_POR_MIN

ESTADO_POSITIVO = "positivo"
ESTADO_NEGATIVO = "negativo"
ESTADO_FUERA_DE_RANGO = "fuera_de_rango"
ESTADO_EN_EPISODIO = "en_episodio"
ESTADO_SIN_DATO = "sin_dato"
ESTADO_HORIZONTE_INCOMPLETO = "no_etiquetable_horizonte_incompleto"
ESTADO_COBERTURA_INSUFICIENTE = "no_etiquetable_cobertura_insuficiente"
ESTADOS_ETIQUETABLES = (ESTADO_POSITIVO, ESTADO_NEGATIVO)


def etiquetar(rejillas: pd.DataFrame, episodios: pd.DataFrame, config: ConfiguracionPreventiva) -> pd.DataFrame:
    rango = config.rango
    horizonte_ns = config.horizonte_min * NS_POR_MIN
    puntos_h = config.puntos_horizonte
    incidente_de = (
        dict(zip(episodios["episodio_id"], episodios["incidente_id"], strict=True))
        if "incidente_id" in episodios
        else {}
    )
    partes = []
    for segmento_id, seg in rejillas.groupby("segmento_id", sort=False):
        seg = seg.sort_values("g_ns")
        g = seg["g_ns"].to_numpy()
        t = seg["temperatura"].to_numpy()
        n = g.size
        valido = np.isfinite(t)
        en_rango = valido & (t >= rango.minimo_celsius) & (t <= rango.maximo_celsius)

        eps = episodios[episodios["segmento_id"] == segmento_id].sort_values("inicio_ns")
        inicios = eps["inicio_ns"].to_numpy()
        fines = eps["fin_ns"].to_numpy()
        ids = eps["episodio_id"].to_numpy()

        episodio_actual = np.full(n, None, dtype=object)
        for ini, fin, eid in zip(inicios, fines, ids, strict=True):
            episodio_actual[(g >= ini) & (g <= fin)] = eid

        # Próximo inicio de episodio estrictamente posterior a cada t.
        pos = np.searchsorted(inicios, g, side="right")
        espera_ns = np.full(n, np.iinfo(np.int64).max)
        if inicios.size:
            hay_siguiente = pos < inicios.size
            siguiente = inicios[np.minimum(pos, inicios.size - 1)]
            espera_ns = np.where(hay_siguiente, siguiente - g, espera_ns)

        # Cobertura válida en (t, t + H]: el segmento es contiguo en la
        # rejilla, así que el horizonte son los `puntos_h` puntos siguientes.
        acumulado = np.concatenate([[0], np.cumsum(valido)])
        indice = np.arange(n)
        horizonte_completo = indice + puntos_h <= n - 1
        fin_h = np.minimum(indice + puntos_h, n - 1)
        cobertura = (acumulado[fin_h + 1] - acumulado[indice + 1]) / puntos_h

        estado = np.full(n, ESTADO_SIN_DATO, dtype=object)
        y = np.full(n, np.nan)
        minutos = np.full(n, np.nan)
        objetivo = np.full(n, None, dtype=object)

        candidato = en_rango & (episodio_actual == None)  # noqa: E711 — comparación elemento a elemento
        positivo = candidato & (espera_ns <= horizonte_ns)
        negativo = candidato & ~positivo & horizonte_completo & (cobertura >= config.cobertura_min_horizonte)
        incompleto = candidato & ~positivo & ~horizonte_completo
        poca_cobertura = candidato & ~positivo & horizonte_completo & ~negativo

        estado[valido & ~en_rango] = ESTADO_FUERA_DE_RANGO
        estado[en_rango & (episodio_actual != None)] = ESTADO_EN_EPISODIO  # noqa: E711
        estado[positivo] = ESTADO_POSITIVO
        estado[negativo] = ESTADO_NEGATIVO
        estado[incompleto] = ESTADO_HORIZONTE_INCOMPLETO
        estado[poca_cobertura] = ESTADO_COBERTURA_INSUFICIENTE
        y[positivo] = 1.0
        y[negativo] = 0.0
        minutos[positivo] = espera_ns[positivo] / NS_POR_MIN
        if inicios.size:
            objetivo[positivo] = ids[pos[positivo]]

        partes.append(
            seg.assign(
                estado_etiqueta=estado,
                y=y,
                minutos_a_excursion=minutos,
                episodio_objetivo=objetivo,
                episodio_actual=episodio_actual,
            )
        )
    salida = pd.concat(partes, ignore_index=True)
    salida["incidente_objetivo"] = salida["episodio_objetivo"].map(incidente_de)
    salida["incidente_actual"] = salida["episodio_actual"].map(incidente_de)
    return salida
