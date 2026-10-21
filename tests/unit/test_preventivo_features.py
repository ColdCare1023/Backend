"""Causalidad y paridad del extractor de features preventivo."""

import numpy as np
import pandas as pd
import pytest

from src.infrastructure.ai.prediccion_preventiva import calidad
from src.infrastructure.ai.prediccion_preventiva.configuracion import (
    CALIDAD_HISTORIAL_INSUFICIENTE,
    CALIDAD_OK,
    ConfiguracionPreventiva,
)
from src.infrastructure.ai.prediccion_preventiva.features import (
    CONJUNTO_TERMICO,
    CONJUNTO_TERMICO_PUERTA,
    features_de_rejillas,
    features_en_instante,
    nombres_features,
)
from src.infrastructure.ai.prediccion_preventiva.ingesta import a_epoch_ns
from tests.unit._preventivo_sintetico import lecturas, por_minuto

CONFIG = ConfiguracionPreventiva()


def _serie_realista(paso_s: float, semilla: int) -> pd.DataFrame:
    rng = np.random.default_rng(semilla)
    minutos = 180
    base = por_minuto([(5.0, 5.2, 60), (5.2, 7.9, 30), (7.9, 6.0, 90)])
    t = np.arange(0, minutos * 60, paso_s)
    valores = np.interp(t / 60, np.arange(minutos), base) + rng.normal(0, 0.05, t.size)
    df = lecturas(valores, paso_s=paso_s, jitter_s=min(1.0, paso_s / 10), semilla=semilla)
    # Dos huecos: uno corto (tolerado) y uno largo (corta el segmento).
    corto = (df["timestamp"] > df["timestamp"].iloc[0] + pd.Timedelta(minutes=50)) & (
        df["timestamp"] < df["timestamp"].iloc[0] + pd.Timedelta(minutes=51, seconds=30)
    )
    largo = (df["timestamp"] > df["timestamp"].iloc[0] + pd.Timedelta(minutes=120)) & (
        df["timestamp"] < df["timestamp"].iloc[0] + pd.Timedelta(minutes=130)
    )
    return df[~corto & ~largo].reset_index(drop=True)


@pytest.mark.parametrize(("paso_s", "semilla"), [(5, 1), (30, 2), (60, 3)])
def test_paridad_entre_entrenamiento_e_inferencia(paso_s, semilla):
    """Para cada punto de la rejilla, la función de producción, que recibe el
    historial crudo hasta t, produce exactamente las mismas features que el
    camino vectorizado de entrenamiento."""
    crudas = _serie_realista(paso_s, semilla)
    rejillas = calidad.preparar_rejillas(crudas, CONFIG)
    x_ent, ok_ent = features_de_rejillas(rejillas, CONFIG)
    ts = a_epoch_ns(crudas["timestamp"])
    temps = crudas["temperatura_interna"].to_numpy()
    comparados = 0
    for i in range(0, len(rejillas), 3):
        g = int(rejillas["g_ns"].iloc[i])
        vector, estado = features_en_instante(ts, temps, None, g, CONFIG)
        assert (vector is not None) == bool(ok_ent[i]), f"Calidad distinta en el punto {i}"
        if vector is not None:
            np.testing.assert_allclose(vector, x_ent[i], rtol=0, atol=1e-9)
            assert estado == CALIDAD_OK
            comparados += 1
    assert comparados > 20


def test_features_no_usan_informacion_futura():
    crudas = lecturas(por_minuto([(5.0, 5.0, 30), (5.0, 7.5, 30)]))
    t = int(a_epoch_ns(crudas["timestamp"])[25])
    ts = a_epoch_ns(crudas["timestamp"])
    original = crudas["temperatura_interna"].to_numpy()
    alterado = original.copy()
    alterado[26:] = 30.0  # futuro absurdo
    a, _ = features_en_instante(ts, original, None, t, CONFIG)
    b, _ = features_en_instante(ts, alterado, None, t, CONFIG)
    np.testing.assert_array_equal(a, b)


def test_ventana_no_mira_antes_de_su_limite():
    crudas = lecturas(por_minuto([(5.0, 5.0, 60)]))
    ts = a_epoch_ns(crudas["timestamp"])
    t = int(ts[50])
    original = crudas["temperatura_interna"].to_numpy()
    alterado = original.copy()
    # 15 min de ventana + 90 s de agregación: lo anterior a t - 17 min no cuenta.
    alterado[:33] = -20.0
    a, _ = features_en_instante(ts, original, None, t, CONFIG)
    b, _ = features_en_instante(ts, alterado, None, t, CONFIG)
    np.testing.assert_array_equal(a, b)


def test_las_features_no_mezclan_series():
    s1 = lecturas(por_minuto([(5.0, 7.0, 60)]), serie_id="S1")
    s2 = lecturas(por_minuto([(6.0, 3.0, 60)]), serie_id="S2")
    juntas = calidad.preparar_rejillas(pd.concat([s1, s2]), CONFIG)
    sola = calidad.preparar_rejillas(s1, CONFIG)
    x_juntas, _ = features_de_rejillas(juntas, CONFIG)
    x_sola, _ = features_de_rejillas(sola, CONFIG)
    np.testing.assert_allclose(x_juntas[juntas["serie_id"].to_numpy() == "S1"], x_sola, equal_nan=True)


def test_las_ventanas_no_atraviesan_un_corte():
    df = lecturas(np.full(120, 5.0))
    df = df[(df.index < 40) | (df.index >= 55)]
    rejillas = calidad.preparar_rejillas(df, CONFIG)
    _, ok = features_de_rejillas(rejillas, CONFIG)
    segundo = rejillas["segmento_id"].to_numpy() == rejillas["segmento_id"].iloc[-1]
    # Tras el corte hacen falta ~13 min de datos nuevos (cobertura ≥ 80 % de 15 min).
    assert not ok[segundo][:12].any()
    assert ok[segundo][-10:].all()


def test_historial_insuficiente_produce_un_estado_explicito():
    crudas = lecturas([5.0, 5.1, 5.2])
    ts = a_epoch_ns(crudas["timestamp"])
    vector, estado = features_en_instante(ts, crudas["temperatura_interna"].to_numpy(), None, int(ts[-1]), CONFIG)
    assert vector is None
    assert estado == CALIDAD_HISTORIAL_INSUFICIENTE


def test_pendiente_en_grados_por_minuto_independiente_de_la_frecuencia():
    for paso in (10, 30, 60):
        minutos = np.arange(0, 40 * 60, paso) / 60
        crudas = lecturas(5.0 + 0.05 * minutos, paso_s=paso)
        ts = a_epoch_ns(crudas["timestamp"])
        vector, _ = features_en_instante(ts, crudas["temperatura_interna"].to_numpy(), None, int(ts[-1]), CONFIG)
        nombres = nombres_features(CONFIG)
        assert vector[nombres.index("pendiente_10m")] == pytest.approx(0.05, abs=1e-6)


def test_variante_con_puerta_exige_la_senal():
    crudas = lecturas(np.full(30, 5.0))
    ts = a_epoch_ns(crudas["timestamp"])
    sin, estado = features_en_instante(ts, crudas["temperatura_interna"].to_numpy(), None, int(ts[-1]), CONFIG, CONJUNTO_TERMICO_PUERTA)
    assert sin is None and estado == CALIDAD_HISTORIAL_INSUFICIENTE
    puertas = np.zeros(30)
    puertas[-3:] = 1
    con, _ = features_en_instante(ts, crudas["temperatura_interna"].to_numpy(), puertas, int(ts[-1]), CONFIG, CONJUNTO_TERMICO_PUERTA)
    nombres = nombres_features(CONFIG, CONJUNTO_TERMICO_PUERTA)
    assert con[nombres.index("puerta_actual")] == 1.0
    assert 0 < con[nombres.index("frac_puerta_15m")] < 1


def test_no_hay_identificadores_ni_variables_incompatibles_entre_las_features():
    nombres = " ".join(nombres_features(CONFIG, CONJUNTO_TERMICO) + nombres_features(CONFIG, CONJUNTO_TERMICO_PUERTA))
    for prohibida in ("serie", "sensor", "fuente", "dataset", "humedad", "hora", "archivo"):
        assert prohibida not in nombres
