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
    CONJUNTO_TERMICO_AMBIENTE,
    CONJUNTO_TERMICO_PUERTA,
    MOTIVO_SIN_COBERTURA,
    MOTIVO_SIN_CONTEXTO_AMBIENTAL,
    evaluar_en_instante,
    features_de_rejillas,
    features_en_instante,
    nombres_features,
)
from src.infrastructure.ai.prediccion_preventiva.ingesta import a_epoch_ns
from tests.unit._preventivo_sintetico import lecturas, por_minuto

CONFIG = ConfiguracionPreventiva()


def _entre(df: pd.DataFrame, desde_min: float, hasta_min: float) -> pd.Series:
    inicio = df["timestamp"].iloc[0]
    return (df["timestamp"] > inicio + pd.Timedelta(minutes=desde_min)) & (
        df["timestamp"] < inicio + pd.Timedelta(minutes=hasta_min)
    )


def _serie_realista(paso_s: float, semilla: int) -> pd.DataFrame:
    """Serie con todas las señales que exporta `scripts/exportar_corridas.py`
    (puerta y SHT31) y los casos que separan entrenamiento y servicio:

    - huecos sin lecturas: uno corto (tolerado) y uno largo (corta el segmento);
    - DS18B20 en falla (temperatura interna NaN) con la puerta y el SHT31
      funcionando: uno corto dentro del segmento y otro de 6 min que corta la
      serie (sus puntos no entran en ninguna ventana de entrenamiento);
    - el SHT31 sin dato (temperatura ambiental y humedad) y luego solo sin
      humedad: la temperatura interna alcanza, el contexto no.
    """
    rng = np.random.default_rng(semilla)
    minutos = 180
    base = por_minuto([(5.0, 5.2, 60), (5.2, 7.9, 30), (7.9, 6.0, 90)])
    t = np.arange(0, minutos * 60, paso_s)
    valores = np.interp(t / 60, np.arange(minutos), base) + rng.normal(0, 0.05, t.size)
    df = lecturas(valores, paso_s=paso_s, jitter_s=min(1.0, paso_s / 10), semilla=semilla)
    df["puerta_abierta"] = (rng.random(len(df)) < 0.1).astype(float)
    df["temperatura_ambiental"] = 22.0 + 1.5 * np.sin(t / 1500.0) + rng.normal(0, 0.2, t.size)
    df["humedad_ambiental"] = 55.0 + rng.normal(0, 1.0, t.size)
    # DS18B20 en falla con la puerta abierta: la puerta y el SHT31 siguen midiendo.
    for desde, hasta in ((30, 31.5), (140, 146)):
        falla = _entre(df, desde, hasta)
        df.loc[falla, "temperatura_interna"] = np.nan
        df.loc[falla, "puerta_abierta"] = 1.0
        df.loc[falla, "temperatura_ambiental"] = 30.0
    df.loc[_entre(df, 80, 88), ["temperatura_ambiental", "humedad_ambiental"]] = np.nan
    df.loc[_entre(df, 100, 104), "humedad_ambiental"] = np.nan
    return df[~_entre(df, 50, 51.5) & ~_entre(df, 120, 130)].reset_index(drop=True)


@pytest.mark.parametrize("conjunto", [CONJUNTO_TERMICO, CONJUNTO_TERMICO_PUERTA, CONJUNTO_TERMICO_AMBIENTE])
@pytest.mark.parametrize(("paso_s", "semilla"), [(5, 1), (30, 2), (60, 3)])
def test_paridad_entre_entrenamiento_e_inferencia(paso_s, semilla, conjunto):
    """Para cada punto de la rejilla, la función de producción, que recibe el
    historial crudo hasta t (con puerta y SHT31), produce exactamente las mismas
    features y la misma calidad que el camino vectorizado de entrenamiento, en
    los tres conjuntos de features."""
    crudas = _serie_realista(paso_s, semilla)
    rejillas = calidad.preparar_rejillas(crudas, CONFIG)
    x_ent, ok_ent = features_de_rejillas(rejillas, CONFIG, conjunto)
    ts = a_epoch_ns(crudas["timestamp"])
    temps = crudas["temperatura_interna"].to_numpy()
    puertas = crudas["puerta_abierta"].to_numpy()
    ambiente = crudas["temperatura_ambiental"].to_numpy()
    humedad = crudas["humedad_ambiental"].to_numpy()
    comparados = 0
    for i in range(len(rejillas)):
        g = int(rejillas["g_ns"].iloc[i])
        vector, estado = features_en_instante(ts, temps, puertas, g, CONFIG, conjunto, ambiente, humedad)
        assert (vector is not None) == bool(ok_ent[i]), f"Calidad distinta en el punto {i}"
        if vector is not None:
            np.testing.assert_allclose(vector, x_ent[i], rtol=0, atol=1e-9)
            assert estado == CALIDAD_OK
            comparados += 1
    assert comparados > 60


def test_sin_sht31_en_t_el_motivo_es_el_contexto_ambiental():
    """Donde la temperatura interna alcanza (`termico_minimo` es ok) pero falta
    el SHT31, `termico_ambiente` no predice y lo dice con su propio motivo; sin
    cobertura térmica se conserva el motivo de siempre."""
    crudas = _serie_realista(30, 2)
    rejillas = calidad.preparar_rejillas(crudas, CONFIG)
    _, ok_termico = features_de_rejillas(rejillas, CONFIG, CONJUNTO_TERMICO)
    _, ok_ambiente = features_de_rejillas(rejillas, CONFIG, CONJUNTO_TERMICO_AMBIENTE)
    ts = a_epoch_ns(crudas["timestamp"])
    senales = (
        crudas["temperatura_interna"].to_numpy(), crudas["puerta_abierta"].to_numpy(),
    )
    contexto = (crudas["temperatura_ambiental"].to_numpy(), crudas["humedad_ambiental"].to_numpy())
    motivos = []
    for i in np.flatnonzero(~ok_ambiente):
        g = int(rejillas["g_ns"].iloc[i])
        vector, estado, motivo = evaluar_en_instante(ts, *senales, g, CONFIG, CONJUNTO_TERMICO_AMBIENTE, *contexto)
        assert vector is None and estado == CALIDAD_HISTORIAL_INSUFICIENTE
        assert motivo == (MOTIVO_SIN_CONTEXTO_AMBIENTAL if ok_termico[i] else MOTIVO_SIN_COBERTURA), i
        motivos.append(motivo)
    # La serie tiene los dos casos: el SHT31 sin dato (ambos canales y solo humedad).
    assert motivos.count(MOTIVO_SIN_CONTEXTO_AMBIENTAL) >= 4
    assert MOTIVO_SIN_COBERTURA in motivos


def test_el_servicio_corta_la_ventana_donde_el_entrenamiento_corta_la_serie():
    """Un DS18B20 en falla durante más de `hueco_max_s` corta la serie: sus
    puntos no entran en ninguna ventana de entrenamiento aunque la puerta y el
    SHT31 sigan midiendo. 14 min después de la falla la ventana de 15 min aún
    toca su último punto; el servicio no debe contar su puerta ni su SHT31."""
    minutos = 60
    df = lecturas(np.full(minutos, 5.0))
    df["puerta_abierta"] = 0.0
    df["temperatura_ambiental"] = 22.0
    df["humedad_ambiental"] = 55.0
    falla = (df.index >= 20) & (df.index < 26)
    df.loc[falla, ["temperatura_interna", "puerta_abierta", "temperatura_ambiental"]] = [np.nan, 1.0, 35.0]
    rejillas = calidad.preparar_rejillas(df, CONFIG)
    t = int(a_epoch_ns(df["timestamp"])[26 + 14])
    (i,) = np.flatnonzero(rejillas["g_ns"].to_numpy() == t)
    ts = a_epoch_ns(df["timestamp"])
    for conjunto, feature in ((CONJUNTO_TERMICO_PUERTA, "frac_puerta_15m"), (CONJUNTO_TERMICO_AMBIENTE, "media_temp_amb_15m")):
        x_ent, ok_ent = features_de_rejillas(rejillas, CONFIG, conjunto)
        assert ok_ent[i]
        vector, _ = features_en_instante(
            ts, df["temperatura_interna"].to_numpy(), df["puerta_abierta"].to_numpy(), t, CONFIG, conjunto,
            df["temperatura_ambiental"].to_numpy(), df["humedad_ambiental"].to_numpy(),
        )
        np.testing.assert_allclose(vector, x_ent[i], rtol=0, atol=1e-9)
        # 15 puntos del segmento nuevo; el primero agrega la última lectura en
        # falla (puerta 1, 35 °C) con la primera válida (puerta 0, 22 °C).
        esperado = 0.5 / 15 if conjunto == CONJUNTO_TERMICO_PUERTA else (28.5 + 22.0 * 14) / 15
        assert vector[nombres_features(CONFIG, conjunto).index(feature)] == pytest.approx(esperado, abs=1e-12)


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
