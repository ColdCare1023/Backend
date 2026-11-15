"""HU-17 (ronda 3, verificación de la pista «features»): el modelo preventivo
recibe TODO el historial que usa el entrenamiento, a cualquier intervalo de
captura admitido, y con la misma limpieza de duplicados.

Antes:
- `PredecirRiesgoPreventivoUseCase` pedía las 120 lecturas anteriores a t. La
  paridad exacta necesita las de los 1170 s previos (15 min de ventana + 180 s
  de segmentación + 90 s de agregación): 120 filas solo alcanzan con un
  intervalo de ~9,75 s o más, y el backend admite nodos a 5 s (HU-54). A 9 s,
  con una falla corta del DS18B20 que el entrenamiento no corta, el servicio
  veía un punto vacío de más y cortaba la ventana (`frac_puerta_15m` 0,0267
  frente a 0,0875; `media_temp_amb_15m` 22,35 frente a 23,14). A 5 s ni
  siquiera alcanzaba la ventana de 15 min.
- `servicio.predecir` promediaba dos lecturas de la misma hora con
  temperaturas distintas, que `calidad.limpiar` (entrenamiento) descarta, y
  contaba dos veces un duplicado exacto.

Ahora el caso de uso pide el historial por alcance temporal
(`desde = t - servicio.alcance_historial()`, del manifiesto cargado) con un
tope de filas solo defensivo, y el servicio aplica `calidad.limpiar`.
"""

import bisect
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest
import sklearn
from sklearn.ensemble import RandomForestClassifier

from src.application.use_cases import predecir_riesgo_preventivo as modulo_caso
from src.application.use_cases.gestionar_dispositivos import ActualizarIntervaloReporteUseCase
from src.application.use_cases.predecir_riesgo_preventivo import PredecirRiesgoPreventivoUseCase
from src.domain.entities.lectura_termica import LecturaTermica
from src.infrastructure.ai.prediccion_preventiva import calidad, registro
from src.infrastructure.ai.prediccion_preventiva.configuracion import (
    CALIDAD_HISTORIAL_INSUFICIENTE,
    CALIDAD_OK,
    ConfiguracionPreventiva,
)
from src.infrastructure.ai.prediccion_preventiva.features import (
    CONJUNTO_TERMICO,
    CONJUNTO_TERMICO_AMBIENTE,
    CONJUNTO_TERMICO_PUERTA,
    CONJUNTOS,
    alcance_historial_s,
    features_de_rejillas,
    nombres_features,
    puntos_previos_rejilla,
)
from src.infrastructure.ai.prediccion_preventiva.ingesta import a_epoch_ns
from src.infrastructure.ai.prediccion_preventiva.servicio import MODO_SOMBRA, ServicioPrediccionPreventiva

CONFIG = ConfiguracionPreventiva()
INICIO = pd.Timestamp("2026-01-05 00:00:00", tz="UTC")
DEVICE = "ESP32-HU17-ALCANCE"


# ── Dobles y utilidades ─────────────────────────────────────────────────────


class _RepoMemoria:
    """Doble de `ILecturaRepository.listar_recientes_por_device` con la
    semántica de la consulta SQL: telemetría del nodo, `timestamp < hasta`,
    `timestamp >= desde`, orden descendente y tope de filas."""

    def __init__(self, lecturas: list[LecturaTermica]) -> None:
        self._lecturas = sorted(lecturas, key=lambda lec: lec.timestamp)
        self._ts = [lec.timestamp for lec in self._lecturas]
        self.llamadas: list[dict] = []

    async def listar_recientes_por_device(self, device_id, limite, solo_dispositivo=True, hasta=None, desde=None):
        self.llamadas.append({"limite": limite, "hasta": hasta, "desde": desde})
        a = 0 if desde is None else bisect.bisect_left(self._ts, desde)
        b = len(self._ts) if hasta is None else bisect.bisect_left(self._ts, hasta)
        filas = [
            lec for lec in self._lecturas[a:b]
            if lec.device_id == device_id and not (solo_dispositivo and lec.es_manual)
        ]
        return filas[::-1][:limite]


def _version(conjunto: str, config: ConfiguracionPreventiva) -> str:
    return f"rf-alcance-{conjunto.replace('_', '-')}-{config.ventana_max_min}m-1.0.0"


def _servicio(raiz, conjunto: str, config: ConfiguracionPreventiva = CONFIG) -> ServicioPrediccionPreventiva:
    """Bosque de juguete registrado con la CLI para `conjunto` y `config`."""
    version = _version(conjunto, config)
    nombres = nombres_features(config, conjunto)
    x = np.random.default_rng(0).normal(0, 1, (120, len(nombres)))
    modelo = RandomForestClassifier(n_estimators=3, random_state=0).fit(x, (x[:, 0] > 0).astype(int))
    registro.registrar(modelo, {
        "version": version, "estado": "EXPERIMENTAL", "conjunto_features": conjunto, "features": nombres,
        "configuracion": config.como_dict(), "umbral_decision": 0.5, "puntuacion_calibrada": False,
        "versiones": {"scikit_learn": sklearn.__version__},
    }, raiz)
    sha = registro.leer_manifiesto(version, raiz)["artefacto"]["sha256"]
    return ServicioPrediccionPreventiva(MODO_SOMBRA, version, sha, raiz=raiz)


def _o_none(v):
    return None if pd.isna(v) else float(v)


def _a_lecturas(df: pd.DataFrame) -> list[LecturaTermica]:
    return [
        LecturaTermica(
            device_id=DEVICE, timestamp=f.timestamp.to_pydatetime(),
            temperatura_ambiental=_o_none(f.temperatura_ambiental), humedad_ambiental=_o_none(f.humedad_ambiental),
            temperatura_interna=_o_none(f.temperatura_interna),
            apertura_refrigerador=None if pd.isna(f.puerta_abierta) else bool(f.puerta_abierta),
            estado_conectividad="online", time_quality="synced",
            boot_id=None if pd.isna(f.boot_id) else int(f.boot_id),
            seq_no=None if pd.isna(f.seq_no) else int(f.seq_no),
        )
        for f in df.itertuples()
    ]


def _entrenamiento(df: pd.DataFrame, conjunto: str):
    """Vector y calidad del pipeline de entrenamiento, por instante (ns)."""
    rejillas = calidad.preparar_rejillas(calidad.limpiar(df)[0], CONFIG)
    x, ok = features_de_rejillas(rejillas, CONFIG, conjunto)
    return {int(g): (x[i], bool(ok[i])) for i, g in enumerate(rejillas["g_ns"].to_numpy())}


def _base(offsets_s: np.ndarray, temperaturas: np.ndarray) -> pd.DataFrame:
    """Serie en el orden de la exportación de corridas (hora, boot_id, seq_no)."""
    n = offsets_s.size
    return pd.DataFrame({
        "fuente": "s", "entorno_id": "e", "serie_id": "S1",
        # Al microsegundo: la resolución de LecturaTermica.timestamp.
        "timestamp": INICIO + pd.to_timedelta(np.round(offsets_s * 1e6).astype(np.int64), unit="us"),
        "temperatura_interna": temperaturas, "puerta_abierta": 0.0,
        "temperatura_ambiental": 22.0, "humedad_ambiental": 55.0,
        "boot_id": 1, "seq_no": np.arange(n),
    })


async def _comparar_caso_de_uso(tmp_path, df: pd.DataFrame, conjuntos=CONJUNTOS, solo_en_rejilla=True):
    """Para cada lectura (en un punto de la rejilla de 60 s), el vector y la
    calidad del caso de uso con el historial del repositorio son los del
    entrenamiento. Devuelve (comparadas, omitidas, discrepancias)."""
    filas = _a_lecturas(df)
    ts = a_epoch_ns(df["timestamp"])
    repo = _RepoMemoria(filas)
    total = {}
    for conjunto in conjuntos:
        entrenamiento = _entrenamiento(df, conjunto)
        nombres = nombres_features(CONFIG, conjunto)
        caso = PredecirRiesgoPreventivoUseCase(_servicio(tmp_path, conjunto), repo, None)
        comparadas = omitidas = 0
        discrepancias = []
        for i, lectura in enumerate(filas):
            if solo_en_rejilla and int(ts[i]) % 60_000_000_000:
                continue
            if lectura.temperatura_interna is None or lectura.es_excursion_confirmada():
                continue
            p = await caso.predecir(lectura)
            x_ent, ok_ent = entrenamiento.get(int(ts[i]), (None, False))
            if (p.estado == CALIDAD_OK) != ok_ent:
                discrepancias.append((conjunto, str(lectura.timestamp), p.estado, p.motivo, ok_ent))
            elif p.estado == CALIDAD_OK:
                v = np.array([p.vector_features[n] for n in nombres])
                if not np.allclose(v, x_ent, rtol=0, atol=1e-5):
                    discrepancias.append((conjunto, str(lectura.timestamp), "vector distinto"))
                comparadas += 1
            else:
                assert p.estado == CALIDAD_HISTORIAL_INSUFICIENTE
                omitidas += 1
        total[conjunto] = (comparadas, omitidas, discrepancias)
    return total


# ── Alcance del historial ───────────────────────────────────────────────────


def test_el_alcance_por_defecto_son_1170_s():
    """15 min de ventana (15 puntos antes de t) + 180 s de segmentación (3
    puntos) + 90 s de agregación del primer punto."""
    assert puntos_previos_rejilla(CONFIG) == 18
    assert alcance_historial_s(CONFIG) == 1170


def test_el_alcance_sale_del_manifiesto_cargado(tmp_path):
    otra = ConfiguracionPreventiva(ventanas_min=(5, 10, 20))
    assert _servicio(tmp_path, CONJUNTO_TERMICO).alcance_historial() == timedelta(seconds=1170)
    assert _servicio(tmp_path, CONJUNTO_TERMICO, otra).alcance_historial() == timedelta(seconds=(20 + 3) * 60 + 90)


async def test_el_caso_de_uso_pide_el_historial_por_tiempo(tmp_path):
    """`desde = t - alcance`, `hasta = t` y un tope de filas que, al intervalo
    mínimo admitido (5 s), cubre el alcance con margen."""
    df = _base(np.arange(0, 30 * 60, 30.0), np.full(60, 5.0))
    filas = _a_lecturas(df)
    repo = _RepoMemoria(filas)
    caso = PredecirRiesgoPreventivoUseCase(_servicio(tmp_path, CONJUNTO_TERMICO), repo, None)

    await caso.predecir(filas[-1])

    [llamada] = repo.llamadas
    t = filas[-1].timestamp
    assert llamada["hasta"] == t
    assert llamada["desde"] == t - timedelta(seconds=1170)
    assert modulo_caso.INTERVALO_MINIMO_CAPTURA_S == ActualizarIntervaloReporteUseCase.MINIMO_SEGUNDOS == 5
    assert llamada["limite"] == 2 * (1170 // 5) == 468


# ── Paridad a cadencias rápidas (sonda probe_limite_9s / probe_paridad_servicio) ──


def _serie_9s_con_falla_corta() -> tuple[pd.DataFrame, int]:
    """9 s de muestreo; DS18B20 en falla con la puerta abierta y el SHT31 a
    35 °C en (t-1110 s, t-900 s]: 3 puntos de rejilla sin temperatura que el
    entrenamiento NO corta (≤ 3). Con 120 filas el servicio veía además vacío
    el punto t-18 min y cortaba. Devuelve (serie, índice de t)."""
    paso = 9
    off = np.arange(90 * 60 // paso) * paso
    t_idx = 400  # 3600 s: múltiplo de 60 y de 9
    t = off[t_idx]
    df = _base(off.astype(float), 4.0 + 0.0005 * off)
    falla = (off > t - 1110) & (off <= t - 900)
    df.loc[falla, ["temperatura_interna", "puerta_abierta", "temperatura_ambiental"]] = [np.nan, 1.0, 35.0]
    return df, t_idx


@pytest.mark.parametrize(
    ("conjunto", "feature", "esperado"),
    [(CONJUNTO_TERMICO_PUERTA, "frac_puerta_15m", 0.0875), (CONJUNTO_TERMICO_AMBIENTE, "media_temp_amb_15m", 23.1375)],
)
async def test_a_9_s_una_falla_corta_del_ds18b20_no_corta_la_ventana(tmp_path, conjunto, feature, esperado):
    df, t_idx = _serie_9s_con_falla_corta()
    filas = _a_lecturas(df)
    x_ent, ok_ent = _entrenamiento(df, conjunto)[int(a_epoch_ns(df["timestamp"])[t_idx])]
    caso = PredecirRiesgoPreventivoUseCase(_servicio(tmp_path, conjunto), _RepoMemoria(filas), None)

    p = await caso.predecir(filas[t_idx])

    assert ok_ent and p.estado == CALIDAD_OK
    assert p.vector_features[feature] == pytest.approx(esperado, abs=1e-6)
    np.testing.assert_allclose(
        [p.vector_features[n] for n in nombres_features(CONFIG, conjunto)], x_ent, rtol=0, atol=1e-6
    )


def _serie_aleatoria(semilla: int, paso_s: int, minutos: int = 120) -> pd.DataFrame:
    """Serie a `paso_s` con fluctuación del reloj (fuera de los múltiplos de
    60 s), puerta, SHT31, fallas del DS18B20 de 60-400 s (con la puerta
    abierta), caídas del SHT31 (uno o ambos canales) y de la puerta, y huecos
    de comunicación de 40-600 s."""
    rng = np.random.default_rng(semilla)
    n = minutos * 60 // paso_s
    off = np.arange(n) * float(paso_s)
    fuera = (np.arange(n) * paso_s) % 60 != 0
    off[fuera] += rng.uniform(-1, 1, fuera.sum())
    df = _base(off, 5.0 + 2.5 * np.sin(off / 1800.0) + rng.normal(0, 0.05, n))
    df["puerta_abierta"] = (rng.random(n) < 0.1).astype(float)
    df["temperatura_ambiental"] = 22 + rng.normal(0, 0.3, n)
    df["humedad_ambiental"] = 55 + rng.normal(0, 1.0, n)
    for _ in range(4):
        a, d = rng.uniform(5, minutos - 10) * 60, rng.choice([60, 150, 170, 185, 200, 240, 400])
        m = (off >= a) & (off < a + d)
        df.loc[m, ["temperatura_interna", "puerta_abierta"]] = [np.nan, 1.0]
    for _ in range(4):
        a, d = rng.uniform(5, minutos - 10) * 60, rng.choice([30, 60, 90, 120, 300])
        cual = rng.choice(["temperatura_ambiental", "humedad_ambiental", "ambos"])
        cols = ["temperatura_ambiental", "humedad_ambiental"] if cual == "ambos" else [cual]
        df.loc[(off >= a) & (off < a + d), cols] = np.nan
    a = rng.uniform(5, minutos - 10) * 60
    df.loc[(off >= a) & (off < a + 120), "puerta_abierta"] = np.nan
    conservar = np.ones(n, dtype=bool)
    for _ in range(3):
        a, d = rng.uniform(5, minutos - 10) * 60, rng.choice([40, 120, 175, 185, 240, 600])
        conservar &= ~((off > a) & (off < a + d))
    return df[conservar].reset_index(drop=True)


@pytest.mark.parametrize("paso_s", [5, 9])
@pytest.mark.parametrize("semilla", [0, 1])
async def test_paridad_del_caso_de_uso_a_5_y_9_s(tmp_path, paso_s, semilla):
    """De punta a punta: historial del repositorio (por alcance) + servicio
    real frente a `calidad.limpiar` + `preparar_rejillas` +
    `features_de_rejillas`, en los tres conjuntos. Con el tope de 120 filas
    (600 s a 5 s) ninguna lectura alcanzaba la ventana de 15 min. A 9 s solo
    una de cada 20 lecturas cae en un punto de la rejilla de entrenamiento."""
    total = await _comparar_caso_de_uso(tmp_path, _serie_aleatoria(semilla, paso_s))

    for conjunto, (comparadas, omitidas, discrepancias) in total.items():
        assert discrepancias == [], (conjunto, discrepancias[:5])
        assert comparadas >= (40 if paso_s == 5 else 10), (conjunto, comparadas, omitidas)
        assert omitidas > 0  # las fallas y los huecos también se comparan


# ── Duplicados: la misma limpieza que el entrenamiento (sonda probe_duplicados) ──


def _serie_con_duplicados(solo_conflicto: bool = False) -> pd.DataFrame:
    """40 min a 30 s (boot 1, seq creciente) con, en el orden de la
    exportación de corridas:
    - min 30: otra lectura a la misma hora con 7,5 °C (conflicto: ambas fuera);
    - min 25: un duplicado exacto (misma hora y temperatura) con la puerta
      cerrada, tras el original con la puerta abierta (cuenta el primero);
    - min 20: dos duplicados exactos y una tercera temperatura (todas fuera).
    `solo_conflicto` deja solo el del min 30 (la serie de `probe_duplicados`)."""
    off = np.arange(0, 40 * 60, 30.0)
    df = _base(off, 5.0 + 0.0005 * off)
    extras = []
    casos = ((30, 7.5, 0.0), (25, None, 0.0), (20, None, 0.0), (20, 6.9, 0.0))
    for minuto, temp, puerta in casos[:1] if solo_conflicto else casos:
        (i,) = np.flatnonzero(off == minuto * 60)
        fila = df.iloc[[i]].copy()
        fila["seq_no"] = 1000 + len(extras)
        fila["puerta_abierta"] = puerta
        if temp is not None:
            fila["temperatura_interna"] = temp
        extras.append(fila)
    if not solo_conflicto:
        df.loc[off == 25 * 60, "puerta_abierta"] = 1.0
    df = pd.concat([df, *extras]).sort_values(["timestamp", "boot_id", "seq_no"], kind="stable")
    return df.reset_index(drop=True)


@pytest.mark.parametrize("conjunto", [CONJUNTO_TERMICO, CONJUNTO_TERMICO_PUERTA])
def test_el_servicio_limpia_los_duplicados_como_el_entrenamiento(tmp_path, conjunto):
    """`predecir` recibe el historial como lo entrega el repositorio
    (descendente por hora; a igual hora, en cualquier orden: aquí el inverso
    del de la exportación) y produce el vector del entrenamiento. Antes,
    `media_5m` salía 6,056 frente a 5,991 y la puerta del duplicado contaba."""
    df = _serie_con_duplicados()
    entrenamiento = _entrenamiento(df, conjunto)
    filas = _a_lecturas(df)
    ts = a_epoch_ns(df["timestamp"])
    servicio = _servicio(tmp_path, conjunto)
    nombres = nombres_features(CONFIG, conjunto)
    repetidas = pd.Series(ts).duplicated(keep=False).to_numpy()
    comparadas = 0
    for i, lectura in enumerate(filas):
        # Solo puntos de la rejilla. Una lectura cuya PROPIA hora se repite no
        # se compara: el historial es estrictamente anterior a t, así que el
        # servicio no ve a su gemela (excepción documentada en 02 §5).
        if int(ts[i]) % 60_000_000_000 or repetidas[i]:
            continue
        historial = [h for h in reversed(filas[:i]) if h.timestamp < lectura.timestamp]
        p = servicio.predecir(lectura, historial)
        x_ent, ok_ent = entrenamiento.get(int(ts[i]), (None, False))
        if not ok_ent:
            continue
        assert p.estado == CALIDAD_OK, (str(lectura.timestamp), p.motivo)
        np.testing.assert_allclose([p.vector_features[n] for n in nombres], x_ent, rtol=0, atol=1e-6)
        comparadas += 1
    assert comparadas >= 20


def test_el_punto_de_la_sonda_de_duplicados(tmp_path):
    """Sonda `probe_duplicados.py` (su columna 4 es `media_5m`): en el min 36,
    el servicio da la del entrenamiento (5,99125), no la que promedia el
    conflicto del min 30 (6,056042)."""
    df = _serie_con_duplicados(solo_conflicto=True)
    filas = _a_lecturas(df)
    i = max(j for j, lec in enumerate(filas) if lec.timestamp == (INICIO + pd.Timedelta(minutes=36)).to_pydatetime())
    historial = [h for h in reversed(filas[:i]) if h.timestamp < filas[i].timestamp]

    p = _servicio(tmp_path, CONJUNTO_TERMICO).predecir(filas[i], historial)

    assert p.estado == CALIDAD_OK
    assert p.vector_features["media_5m"] == pytest.approx(5.99125, abs=1e-6)
