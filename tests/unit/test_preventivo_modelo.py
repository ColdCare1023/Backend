"""Partición sin fugas, entrenamiento reproducible y métricas."""

import numpy as np
import pandas as pd
import pytest

from src.infrastructure.ai.prediccion_preventiva import calidad, eventos
from src.infrastructure.ai.prediccion_preventiva.configuracion import ConfiguracionPreventiva
from src.infrastructure.ai.prediccion_preventiva.entrenamiento import (
    buscar_hiperparametros,
    construir_observaciones,
    crear_modelo,
)
from src.infrastructure.ai.prediccion_preventiva.etiquetas import etiquetar
from src.infrastructure.ai.prediccion_preventiva.evaluacion import (
    elegir_umbral_f1,
    metricas_evento,
    metricas_observacion,
)
from src.infrastructure.ai.prediccion_preventiva.particion import (
    auditar_particion,
    dividir_desarrollo_prueba,
    pliegues_walk_forward,
)
from tests.unit._preventivo_sintetico import INICIO, lecturas

CONFIG = ConfiguracionPreventiva()
PARAMS = {"max_depth": 4, "min_samples_leaf": 5, "max_features": "sqrt", "class_weight": "balanced_subsample"}


def _dataset():
    """Cuatro días sintéticos con un episodio al día precedido de una rampa."""
    rng = np.random.default_rng(7)
    dia = np.concatenate([
        np.full(600, 5.0), np.linspace(5.0, 8.0, 40), np.full(8, 9.0), np.linspace(8.0, 5.0, 60), np.full(732, 5.0)
    ])
    valores = np.tile(dia, 4) + rng.normal(0, 0.03, dia.size * 4)
    rejillas = calidad.preparar_rejillas(lecturas(valores), CONFIG)
    episodios = eventos.agrupar_incidentes(eventos.detectar_episodios(rejillas, CONFIG), CONFIG)
    etiquetadas = etiquetar(rejillas, episodios, CONFIG)
    return construir_observaciones(etiquetadas, CONFIG), episodios


def test_particion_temporal_sin_fugas():
    obs, _ = _dataset()
    dev, prueba = dividir_desarrollo_prueba(obs.tabla, INICIO + pd.Timedelta(days=3), CONFIG)
    auditoria = auditar_particion(obs.tabla, {"desarrollo": dev, "prueba": prueba}, CONFIG)
    assert auditoria["aprobada"], auditoria["comprobaciones"]
    assert obs.tabla["g_ns"].iloc[dev].max() < obs.tabla["g_ns"].iloc[prueba].min()


def test_auditoria_detecta_una_particion_barajada():
    obs, _ = _dataset()
    idx = np.random.default_rng(0).permutation(len(obs.tabla))
    mitad = len(idx) // 2
    auditoria = auditar_particion(obs.tabla, {"a": idx[:mitad], "b": idx[mitad:]}, CONFIG)
    assert not auditoria["aprobada"]
    assert auditoria["comprobaciones"]["ventanas_solapadas_entre_particiones"] > 0
    assert auditoria["comprobaciones"]["incidentes_compartidos"] > 0


def test_walk_forward_nunca_entrena_con_futuro():
    obs, _ = _dataset()
    cortes = [INICIO + pd.Timedelta(days=d) for d in (1, 2)]
    pliegues = pliegues_walk_forward(obs.tabla, cortes, INICIO + pd.Timedelta(days=3), CONFIG)
    g = obs.tabla["g_ns"].to_numpy()
    for p in pliegues:
        assert g[p.entrenamiento].max() + CONFIG.horizonte_min * 60e9 < g[p.validacion].min()
        assert auditar_particion(obs.tabla, {"e": p.entrenamiento, "v": p.validacion}, CONFIG)["aprobada"]


def test_entrenamiento_reproducible_con_la_misma_semilla_y_dimensiones():
    """Misma semilla → mismos árboles. predict_proba en paralelo suma los
    árboles en orden variable (diferencias ~1e-16); con n_jobs=1 es idéntica."""
    obs, _ = _dataset()
    m1 = crear_modelo(PARAMS, semilla=42).fit(obs.x, obs.y)
    m2 = crear_modelo(PARAMS, semilla=42).fit(obs.x, obs.y)
    for t1, t2 in zip(m1.estimators_, m2.estimators_, strict=True):
        np.testing.assert_array_equal(t1.tree_.threshold, t2.tree_.threshold)
        np.testing.assert_array_equal(t1.tree_.feature, t2.tree_.feature)
    m1.set_params(n_jobs=1)
    m2.set_params(n_jobs=1)
    a, b = m1.predict_proba(obs.x), m2.predict_proba(obs.x)
    np.testing.assert_array_equal(a, b)
    assert a.shape == (len(obs.tabla), 2)
    assert obs.x.shape[1] == len(obs.nombres)


def test_desbalance_y_umbral_de_validacion():
    obs, _ = _dataset()
    assert obs.y.mean() < 0.1
    p = crear_modelo(PARAMS).fit(obs.x, obs.y).predict_proba(obs.x)[:, 1]
    umbral = elegir_umbral_f1(obs.y, p)
    assert 0 < umbral["umbral"] <= 1 and umbral["f1"] > 0


def test_metricas_con_una_sola_clase_son_no_estimables_no_cero():
    m = metricas_observacion(np.zeros(10, dtype=int), np.linspace(0, 1, 10), 0.5, True)
    assert m["average_precision"]["estado"] == "no estimable"
    assert m["roc_auc"]["estado"] == "no estimable"
    assert elegir_umbral_f1(np.zeros(5), np.ones(5))["umbral"] is None


def test_metricas_por_evento_anticipacion_y_falsas_alarmas():
    obs, episodios = _dataset()
    alarma = np.array(obs.tabla["minutos_a_excursion"].le(12).to_numpy(), dtype=bool)
    alarma[:3] = True  # una falsa alarma aislada al inicio
    ev = metricas_evento(obs.tabla, alarma, episodios, CONFIG)
    assert ev["episodios_evaluables"] == 4
    assert ev["episodios_anticipados"] == 4
    assert ev["anticipacion_min"]["mediana"] == 12
    assert ev["falsas_alarmas"] == 1
    assert ev["sensibilidad_por_incidente"]["ic95_exacto"][0] < 1.0


def test_busqueda_de_hiperparametros_con_rejilla_minima():
    obs, _ = _dataset()
    cortes = [INICIO + pd.Timedelta(days=d) for d in (1, 2)]
    pliegues = pliegues_walk_forward(obs.tabla, cortes, INICIO + pd.Timedelta(days=4), CONFIG)
    mejor, resultados = buscar_hiperparametros(
        obs.x, obs.y, pliegues, rejilla={k: [v] for k, v in PARAMS.items()}
    )
    assert mejor == PARAMS
    assert resultados[0]["ap_oof"] == pytest.approx(resultados[0]["ap_oof"])
