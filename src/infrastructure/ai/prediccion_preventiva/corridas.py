"""HU-17/HU-18: pipeline formal de entrenamiento con corridas controladas.

Es el método que describen el TI §1.4.1 y el Anexo 2 §2. Las corridas se
generan aparte, en hardware (nodo ESP32 en el refrigerador con excursiones
provocadas), y llegan como CSV exportado de `thermal_readings` con
`corrida_id` (ver `ingesta.importar_coldcare_csv`). Este módulo:

1. etiqueta y = 1 si empieza una excursión en (t, t + H] (etiquetas.py);
2. excluye ventanas incompletas, lecturas con SENSOR_FAULT y lecturas con
   time_quality = unsynced, y CUENTA cada exclusión;
3. divide por corrida, con separación ≥ H entre entrenamiento y prueba;
4. compara el Random Forest con dos baselines: DummyClassifier(prior) y la
   regla de pendiente (extrapolación de la tendencia reciente);
5. reporta las métricas de los DOS juegos de criterios (F1 macro ≥ 0,85 y
   los criterios por incidente) para cada conjunto de features;
6. repite todo con y sin el SHT31 (ablación) y reporta la diferencia.

Uso: `python -m src.infrastructure.ai.prediccion_preventiva corridas <csv>
--prueba C3,C4` (ver __main__.py).
"""

from pathlib import Path

import numpy as np
from sklearn.metrics import average_precision_score

from src.application.use_cases.gestionar_modelo_ia import evaluar_criterios_modelo
from src.infrastructure.ai.prediccion_preventiva import calidad, eventos, ingesta
from src.infrastructure.ai.prediccion_preventiva.configuracion import ConfiguracionPreventiva
from src.infrastructure.ai.prediccion_preventiva.entrenamiento import (
    SEMILLA,
    baseline_prior,
    construir_observaciones,
    crear_modelo,
    marca_tiempo,
    puntuacion_tendencia,
)
from src.infrastructure.ai.prediccion_preventiva.etiquetas import etiquetar
from src.infrastructure.ai.prediccion_preventiva.evaluacion import (
    elegir_umbral_f1,
    metricas_evento,
    metricas_observacion,
)
from src.infrastructure.ai.prediccion_preventiva.features import CONJUNTO_TERMICO, CONJUNTO_TERMICO_AMBIENTE
from src.infrastructure.ai.prediccion_preventiva.particion import dividir_por_corrida

# Hiperparámetros fijos (sin búsqueda): con pocas corridas, una búsqueda solo
# sobreajusta la validación.
PARAMETROS_RF = {"max_depth": 8, "min_samples_leaf": 10, "max_features": "sqrt", "class_weight": "balanced_subsample"}
HORIZONTE_PENDIENTE_MIN = 10


def _metricas_planas(observacion: dict, evento: dict, ap_rf: float | None, ap_base: float | None) -> dict:
    """Las métricas en la forma que espera el registro de versiones (HU-46)."""
    sens = evento.get("sensibilidad_por_incidente") or {}
    ic95 = sens.get("ic95_exacto") if isinstance(sens, dict) else None
    falsas = evento.get("falsas_alarmas_por_dia_sensor")
    anticipacion = evento.get("anticipacion_min") or {}
    return {
        "f1_macro": observacion.get("macro_f1"),
        "incidentes_evaluables": evento.get("incidentes_evaluables"),
        "sensibilidad_incidente": sens.get("valor") if isinstance(sens, dict) else None,
        "ic95_inferior_sensibilidad": ic95[0] if ic95 else None,
        "falsas_alarmas_por_dia_sensor": falsas if isinstance(falsas, float) else None,
        "mediana_anticipacion_min": anticipacion.get("mediana") if isinstance(anticipacion, dict) else None,
        "ap_random_forest": ap_rf,
        "ap_mejor_baseline": ap_base,
    }


def _ap(y: np.ndarray, puntuacion: np.ndarray) -> float | None:
    return float(average_precision_score(y, puntuacion)) if 0 < y.sum() < y.size else None


def evaluar_conjunto(rejillas, episodios, config: ConfiguracionPreventiva, conjunto: str, corridas_prueba: list[str]):
    etiquetadas = etiquetar(rejillas, episodios, config)
    obs = construir_observaciones(etiquetadas, config, conjunto)
    obs.tabla["corrida_id"] = obs.tabla["serie_id"].str.split("#", n=1).str[1]
    particion = dividir_por_corrida(obs.tabla, corridas_prueba, config)
    base = {
        "conjunto": conjunto,
        "observaciones": len(obs.tabla),
        "observaciones_excluidas": obs.excluidas,
        "particiones": particion.como_dict(),
    }
    entrenamiento, prueba = particion.entrenamiento, particion.prueba
    y = obs.y
    if entrenamiento.size == 0 or prueba.size == 0 or len(set(y[entrenamiento])) < 2:
        return {**base, "resultado": "no estimable: entrenamiento sin ambas clases o partición vacía"}

    modelo = crear_modelo(PARAMETROS_RF, SEMILLA, n_jobs=1).fit(obs.x[entrenamiento], y[entrenamiento])
    # El umbral se elige sobre entrenamiento (sin mirar prueba): con pocas
    # corridas no queda una validación independiente que valga la pena.
    umbral = elegir_umbral_f1(y[entrenamiento], modelo.predict_proba(obs.x[entrenamiento])[:, 1])["umbral"] or 0.5
    pendiente = puntuacion_tendencia(obs, HORIZONTE_PENDIENTE_MIN, config)
    umbral_pendiente = elegir_umbral_f1(y[entrenamiento], pendiente[entrenamiento])["umbral"]
    puntuaciones = {
        "random_forest": (modelo.predict_proba(obs.x[prueba])[:, 1], umbral, True),
        "dummy_prior": (baseline_prior(y[entrenamiento], prueba.size), 0.5, True),
        "regla_pendiente": (pendiente[prueba], umbral_pendiente, False),
    }
    tabla_prueba = obs.tabla.iloc[prueba].reset_index(drop=True)
    episodios_prueba = episodios[episodios["serie_id"].isin(tabla_prueba["serie_id"].unique())]
    resultados = {}
    for nombre, (puntuacion, corte, es_probabilidad) in puntuaciones.items():
        if corte is None:
            resultados[nombre] = {"estado": "no estimable", "motivo": "sin umbral"}
            continue
        resultados[nombre] = {
            "umbral": float(corte),
            "average_precision": _ap(y[prueba], puntuacion),
            "observacion": metricas_observacion(y[prueba], puntuacion, corte, es_probabilidad),
            "evento": metricas_evento(tabla_prueba, puntuacion >= corte, episodios_prueba, config),
        }
    rf = resultados["random_forest"]
    ap_bases = [r.get("average_precision") for n, r in resultados.items() if n != "random_forest"]
    mejor_base = max((a for a in ap_bases if a is not None), default=None)
    metricas = _metricas_planas(rf["observacion"], rf["evento"], rf["average_precision"], mejor_base)
    return {
        **base,
        "resultado": "evaluado",
        "prueba": resultados,
        "metricas_registro": metricas,
        "criterios": evaluar_criterios_modelo(metricas),
    }


def ejecutar_pipeline_corridas(
    ruta_csv: Path, corridas_prueba: list[str], config: ConfiguracionPreventiva | None = None
) -> dict:
    config = config or ConfiguracionPreventiva()
    crudas, informe = ingesta.importar_coldcare_csv(ruta_csv)
    lecturas, limpieza = calidad.limpiar(crudas)
    rejillas = calidad.preparar_rejillas(lecturas, config)
    episodios = eventos.agrupar_incidentes(eventos.detectar_episodios(rejillas, config), config)
    resultados = {
        conjunto: evaluar_conjunto(rejillas, episodios, config, conjunto, corridas_prueba)
        for conjunto in (CONJUNTO_TERMICO, CONJUNTO_TERMICO_AMBIENTE)
    }
    sin, con = resultados[CONJUNTO_TERMICO], resultados[CONJUNTO_TERMICO_AMBIENTE]
    ablacion = None
    if sin.get("resultado") == con.get("resultado") == "evaluado":
        ablacion = {
            clave: (
                None if con["metricas_registro"][clave] is None or sin["metricas_registro"][clave] is None
                else con["metricas_registro"][clave] - sin["metricas_registro"][clave]
            )
            for clave in ("f1_macro", "sensibilidad_incidente", "falsas_alarmas_por_dia_sensor", "ap_random_forest")
        }
    return {
        "generado_en": marca_tiempo(),
        "configuracion": config.como_dict(),
        "horizonte_min": config.horizonte_min,
        "importacion": informe.como_dict(),
        "limpieza": limpieza.como_dict(),
        "corridas": sorted(lecturas["serie_id"].unique().tolist()),
        "incidentes_independientes": int(episodios["incidente_id"].nunique()) if len(episodios) else 0,
        "resultados": resultados,
        # Con SHT31 menos sin SHT31: positivo en F1/sensibilidad/AP = aporta.
        "ablacion_sht31": ablacion,
    }
