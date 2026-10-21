"""Validación, limpieza, remuestreo causal y segmentación de series.

Reglas (ver docs/ml/02_PREPROCESSING_AND_FEATURES.md):
- No se interpola ningún valor: un punto de rejilla sin lecturas queda NaN.
- No se imputa con valores futuros.
- Un hueco mayor que `hueco_max_s` corta la serie: ninguna ventana ni
  etiqueta atraviesa un corte de conectividad o un cambio de sensor.
"""

from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.infrastructure.ai.prediccion_preventiva.configuracion import ConfiguracionPreventiva
from src.infrastructure.ai.prediccion_preventiva.ingesta import a_epoch_ns

# Fuera del rango físico de medición del sensor (DS18B20: -55…125 °C;
# DHT22: -40…80 °C) un valor es un fallo del sensor, no una temperatura.
TEMPERATURA_MIN_FISICA_C = -55.0
TEMPERATURA_MAX_FISICA_C = 80.0

# Clasificación del equipo por la mediana de su temperatura. Un
# ultracongelador (-30 °C) no es un refrigerador de 2-8 °C "más frío": su
# dinámica no es comparable y NO se transforma para que lo parezca.
EQUIPO_REFRIGERACION = "refrigeracion_compatible"
EQUIPO_CONGELACION = "congelacion_incompatible"
EQUIPO_ULTRACONGELACION = "ultracongelacion_incompatible"
EQUIPO_SIN_DATOS = "sin_datos"


@dataclass(slots=True)
class InformeLimpieza:
    descartes: Counter = field(default_factory=Counter)
    por_serie: dict[str, dict] = field(default_factory=dict)

    def como_dict(self) -> dict:
        return {"descartes": dict(self.descartes), "por_serie": self.por_serie}


def clasificar_equipo(temperaturas: pd.Series) -> str:
    validas = temperaturas.dropna()
    if validas.empty:
        return EQUIPO_SIN_DATOS
    mediana = float(validas.median())
    if mediana < -10.0:
        return EQUIPO_ULTRACONGELACION
    if mediana < 0.0:
        return EQUIPO_CONGELACION
    return EQUIPO_REFRIGERACION


def perfilar_serie(df: pd.DataFrame, config: ConfiguracionPreventiva) -> dict:
    """Estadísticos de auditoría de UNA serie cruda (antes de limpiar)."""
    ts = pd.to_datetime(df["timestamp"], utc=True)
    temp = df["temperatura_interna"]
    orden = ts.sort_values()
    dt = orden.diff().dt.total_seconds().dropna()
    validas = temp.dropna()
    rango = config.rango
    return {
        "registros": len(df),
        "temperaturas_validas": int(validas.size),
        "nulos_temperatura": int(temp.isna().sum()),
        "inicio_utc": orden.iloc[0].isoformat() if len(orden) else None,
        "fin_utc": orden.iloc[-1].isoformat() if len(orden) else None,
        "duracion_h": round(float((orden.iloc[-1] - orden.iloc[0]).total_seconds() / 3600), 2) if len(orden) else 0.0,
        "intervalo_mediana_s": float(dt.median()) if len(dt) else None,
        "intervalo_p95_s": float(dt.quantile(0.95)) if len(dt) else None,
        "intervalos_irregulares": int(((dt - dt.median()).abs() > 0.5 * dt.median()).sum()) if len(dt) else 0,
        "huecos_mayores_hueco_max": int((dt > config.hueco_max_s).sum()),
        "hueco_max_s": float(dt.max()) if len(dt) else None,
        "timestamps_duplicados": int(ts.duplicated().sum()),
        "timestamps_no_monotonos": int((ts.diff().dt.total_seconds() < 0).sum()),
        "temperaturas_fisicamente_imposibles": int(
            ((validas < TEMPERATURA_MIN_FISICA_C) | (validas > TEMPERATURA_MAX_FISICA_C)).sum()
        ),
        "temp_min": float(validas.min()) if validas.size else None,
        "temp_max": float(validas.max()) if validas.size else None,
        "temp_mediana": float(validas.median()) if validas.size else None,
        "pct_en_rango": round(float(validas.between(rango.minimo_celsius, rango.maximo_celsius).mean() * 100), 2)
        if validas.size
        else None,
        "pct_sobre_max": round(float((validas > rango.maximo_celsius).mean() * 100), 2) if validas.size else None,
        "pct_bajo_min": round(float((validas < rango.minimo_celsius).mean() * 100), 2) if validas.size else None,
        "clase_equipo": clasificar_equipo(temp),
    }


def limpiar(lecturas: pd.DataFrame) -> tuple[pd.DataFrame, InformeLimpieza]:
    """Ordena, elimina duplicados exactos, anula timestamps duplicados con
    valores en conflicto (no hay forma de saber cuál es el verdadero) y
    convierte en NaN las temperaturas físicamente imposibles."""
    informe = InformeLimpieza()
    partes = []
    for serie_id, df in lecturas.groupby("serie_id", sort=True):
        df = df.sort_values("timestamp", kind="stable")
        n0 = len(df)
        df = df.drop_duplicates(subset=["timestamp", "temperatura_interna"], keep="first")
        exactos = n0 - len(df)
        conflicto = df["timestamp"].duplicated(keep=False)
        df = df[~conflicto]
        imposibles = (df["temperatura_interna"] < TEMPERATURA_MIN_FISICA_C) | (
            df["temperatura_interna"] > TEMPERATURA_MAX_FISICA_C
        )
        df = df.assign(temperatura_interna=df["temperatura_interna"].mask(imposibles))
        detalle = {
            "duplicados_exactos": int(exactos),
            "timestamps_en_conflicto": int(conflicto.sum()),
            "temperaturas_fisicamente_imposibles": int(imposibles.sum()),
        }
        informe.por_serie[str(serie_id)] = detalle
        for motivo, cantidad in detalle.items():
            informe.descartes[motivo] += cantidad
        partes.append(df)
    limpio = pd.concat(partes, ignore_index=True) if partes else lecturas.iloc[0:0]
    return limpio, informe


def recortar_periodo(lecturas: pd.DataFrame, inicio: pd.Timestamp | None, fin: pd.Timestamp | None) -> pd.DataFrame:
    mascara = pd.Series(True, index=lecturas.index)
    if inicio is not None:
        mascara &= lecturas["timestamp"] >= inicio
    if fin is not None:
        mascara &= lecturas["timestamp"] <= fin
    return lecturas[mascara]


def asignar_puntos(ts_ns: np.ndarray, ancla_ns: int, paso_ns: int) -> np.ndarray:
    """Primer punto de rejilla g = ancla + k·paso con g ≥ ts.

    Entrenamiento usa ancla = 0 (múltiplos del paso desde la época Unix);
    producción usa ancla = instante de la lectura actual. Con la misma
    función, un punto de entrenamiento y una inferencia en ese mismo
    instante producen exactamente los mismos valores (prueba de paridad)."""
    return ancla_ns + -((ancla_ns - ts_ns) // paso_ns) * paso_ns


def _media_causal(ts_ord: np.ndarray, valores_ord: np.ndarray, rejilla: np.ndarray, agregacion_ns: int):
    """Media y cantidad de lecturas en (g - agregacion, g] para cada g."""
    alto = np.searchsorted(ts_ord, rejilla, side="right")
    bajo = np.searchsorted(ts_ord, rejilla - agregacion_ns, side="right")
    acumulado = np.concatenate([[0.0], np.cumsum(valores_ord)])
    n = alto - bajo
    with np.errstate(invalid="ignore", divide="ignore"):
        media = np.where(n > 0, (acumulado[alto] - acumulado[bajo]) / np.maximum(n, 1), np.nan)
    return media, n


def remuestrear(
    ts_ns: np.ndarray,
    temperaturas: np.ndarray,
    puertas: np.ndarray | None,
    paso_s: int,
    agregacion_s: int,
    ancla_ns: int = 0,
    desde_ns: int | None = None,
    hasta_ns: int | None = None,
) -> pd.DataFrame:
    """Rejilla regular cada `paso_s`: media de temperatura y fracción de
    puerta abierta de las lecturas en (g - agregacion_s, g]; NaN donde no
    hubo lecturas válidas. No interpola ni mira lecturas posteriores a g."""
    paso_ns = paso_s * 1_000_000_000
    agregacion_ns = agregacion_s * 1_000_000_000
    validas = np.isfinite(temperaturas)
    orden = np.argsort(ts_ns[validas], kind="stable")
    ts_v, t_v = ts_ns[validas][orden], temperaturas[validas][orden]
    if desde_ns is None or hasta_ns is None:
        if ts_v.size == 0:
            return pd.DataFrame({"g_ns": [], "temperatura": [], "n_lecturas": [], "puerta": []})
        desde_ns = int(asignar_puntos(ts_v[:1], ancla_ns, paso_ns)[0]) if desde_ns is None else desde_ns
        hasta_ns = int(asignar_puntos(ts_v[-1:], ancla_ns, paso_ns)[0]) if hasta_ns is None else hasta_ns
    rejilla = np.arange(desde_ns, hasta_ns + 1, paso_ns, dtype=np.int64)
    media, n = _media_causal(ts_v, t_v, rejilla, agregacion_ns)
    puerta = np.full(rejilla.size, np.nan)
    if puertas is not None:
        con_puerta = np.isfinite(puertas)
        if con_puerta.any():
            orden_p = np.argsort(ts_ns[con_puerta], kind="stable")
            puerta, _ = _media_causal(ts_ns[con_puerta][orden_p], puertas[con_puerta][orden_p], rejilla, agregacion_ns)
    return pd.DataFrame({"g_ns": rejilla, "temperatura": media, "n_lecturas": n, "puerta": puerta})


def segmentar(rejilla: pd.DataFrame, config: ConfiguracionPreventiva) -> pd.DataFrame:
    """Corta la rejilla donde hay más de `hueco_max_s` sin lecturas. Los
    puntos del hueco largo se eliminan; los huecos cortos quedan NaN dentro
    del segmento (las reglas de cobertura deciden si son tolerables)."""
    if rejilla.empty:
        return rejilla.assign(segmento=pd.Series(dtype=int))
    vacio = rejilla["temperatura"].isna().to_numpy()
    max_vacios = config.hueco_max_s // config.paso_rejilla_s
    # Longitud de cada racha de NaN para decidir si es un corte.
    racha = np.zeros(vacio.size, dtype=int)
    inicio = None
    for i, es_vacio in enumerate(np.append(vacio, False)):
        if es_vacio and inicio is None:
            inicio = i
        elif not es_vacio and inicio is not None:
            racha[inicio:i] = i - inicio
            inicio = None
    corte = vacio & (racha > max_vacios)
    conservar = ~corte
    # Un nuevo segmento empieza en el primer punto conservado tras un corte.
    nuevo = np.zeros(vacio.size, dtype=bool)
    nuevo[0] = True
    nuevo[1:] = corte[:-1] & conservar[1:]
    segmento = np.cumsum(nuevo) - 1
    salida = rejilla.assign(segmento=segmento)[conservar].reset_index(drop=True)
    # Un segmento no empieza ni termina con NaN.
    partes = []
    for _, seg in salida.groupby("segmento", sort=True):
        validos = seg["temperatura"].notna().to_numpy()
        if not validos.any():
            continue
        a, b = np.flatnonzero(validos)[[0, -1]]
        partes.append(seg.iloc[a : b + 1])
    if not partes:
        return salida.iloc[0:0]
    salida = pd.concat(partes, ignore_index=True)
    salida["segmento"] = pd.factorize(salida["segmento"])[0]
    return salida


def preparar_rejillas(lecturas: pd.DataFrame, config: ConfiguracionPreventiva) -> pd.DataFrame:
    """Remuestrea y segmenta cada serie. Devuelve una tabla larga con
    columnas: fuente, entorno_id, serie_id, segmento_id, timestamp, g_ns,
    temperatura, n_lecturas, puerta."""
    partes = []
    for (fuente, entorno, serie), df in lecturas.groupby(["fuente", "entorno_id", "serie_id"], sort=True):
        ts_ns = a_epoch_ns(df["timestamp"])
        temps = df["temperatura_interna"].to_numpy(dtype=float)
        puertas = df["puerta_abierta"].to_numpy(dtype=float)
        rejilla = segmentar(
            remuestrear(ts_ns, temps, puertas, config.paso_rejilla_s, config.agregacion_s), config
        )
        if rejilla.empty:
            continue
        rejilla.insert(0, "segmento_id", [f"{serie}#{s}" for s in rejilla["segmento"]])
        rejilla.insert(0, "serie_id", serie)
        rejilla.insert(0, "entorno_id", entorno)
        rejilla.insert(0, "fuente", fuente)
        partes.append(rejilla.drop(columns=["segmento"]))
    if not partes:
        raise ValueError("Ninguna serie produjo puntos de rejilla válidos")
    tabla = pd.concat(partes, ignore_index=True)
    tabla["timestamp"] = pd.to_datetime(tabla["g_ns"], unit="ns", utc=True)
    return tabla
