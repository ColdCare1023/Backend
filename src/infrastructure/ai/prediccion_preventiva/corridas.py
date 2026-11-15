"""HU-17/HU-18: pipeline formal de entrenamiento con corridas controladas.

Es el método que describen el TI §1.4.1 y el Anexo 2 §2. Las corridas se
generan aparte, en hardware (nodo ESP32 en el refrigerador con excursiones
provocadas), y llegan como CSV exportado de `thermal_readings` con
`corrida_id` (ver `ingesta.importar_coldcare_csv`). Este módulo:

1. etiqueta y = 1 si empieza una excursión en (t, t + H] (etiquetas.py);
2. excluye ventanas incompletas, lecturas con SENSOR_FAULT y lecturas con
   time_quality = unsynced, y CUENTA cada exclusión;
3. divide por corrida, con separación ≥ H entre entrenamiento y prueba;
4. elige el umbral de decisión con puntuaciones FUERA de la muestra de
   entrenamiento, separadas por corrida (P-3, ver `seleccionar_umbral`);
5. compara el Random Forest con dos baselines: DummyClassifier(prior) y la
   regla de pendiente (extrapolación de la tendencia reciente);
6. reporta las métricas de los DOS juegos de criterios (F1 macro ≥ 0,85 y
   los criterios por incidente) para cada conjunto de features;
7. repite todo con y sin el SHT31 (ablación) y reporta la diferencia.

Uso: `python -m src.infrastructure.ai.prediccion_preventiva corridas --csv
corridas.csv --prueba C3,C4 [--validacion C2]` (ver __main__.py). Con
`--registrar --version V` además registra el modelo (registro_corridas.py).
"""

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
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
CONJUNTOS_INFORME = (CONJUNTO_TERMICO, CONJUNTO_TERMICO_AMBIENTE)

# P-3: de dónde salen las puntuaciones con las que se elige el umbral. Nunca
# de filas con las que se ajustó el modelo que las puntúa.
METODO_UMBRAL_PARTICION = "particion_validacion"
METODO_UMBRAL_DEJAR_UNA_FUERA = "dejar_una_corrida_fuera"
DESCRIPCION_METODO_UMBRAL = {
    METODO_UMBRAL_PARTICION: (
        "F1 máximo en las predicciones de las corridas de validación, separadas por corrida (≥ H) "
        "de las de ajuste y de las de prueba; el modelo registrado se ajustó solo con las de ajuste"
    ),
    METODO_UMBRAL_DEJAR_UNA_FUERA: (
        "F1 máximo en predicciones fuera de corrida (dejar una corrida fuera): cada corrida de "
        "entrenamiento la puntúa un modelo ajustado sin ella; el modelo registrado se ajusta después "
        "con todas las corridas de entrenamiento"
    ),
}
# Corridas mínimas para ajustar el modelo: con una sola no hay forma de elegir
# el umbral fuera de muestra (dejar una fuera no tendría con qué entrenar).
MIN_CORRIDAS_AJUSTE = 2


def sin_privados(datos):
    """Copia apta para JSON: sin claves `_privadas` (modelo, tablas) y con los
    tipos de numpy/pandas convertidos."""
    if isinstance(datos, dict):
        return {k: sin_privados(v) for k, v in datos.items() if not str(k).startswith("_")}
    if isinstance(datos, list | tuple):
        return [sin_privados(v) for v in datos]
    if isinstance(datos, np.floating | np.integer | np.bool_):
        return datos.item()
    if isinstance(datos, np.ndarray):
        return datos.tolist()
    if isinstance(datos, pd.Timestamp):
        return datos.isoformat()
    return datos


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


def _ajustar(x: np.ndarray, y: np.ndarray):
    return crear_modelo(PARAMETROS_RF, SEMILLA, n_jobs=1).fit(x, y)


def _ambas_clases(y: np.ndarray) -> bool:
    return len(set(np.asarray(y).tolist())) == 2


@dataclass(frozen=True, slots=True)
class DivisionAjuste:
    """Cómo se reparten las corridas de entrenamiento (las que no son de prueba)
    entre ajustar el modelo y elegir el umbral (P-3). Índices posicionales en
    la tabla de observaciones."""

    metodo: str
    ajuste: np.ndarray
    corridas_ajuste: list[str]
    corridas_validacion: list[str]
    validacion: np.ndarray = field(default_factory=lambda: np.array([], dtype=int))
    corridas_descartadas: dict[str, str] = field(default_factory=dict)
    separacion_validacion_min: float | None = None


def dividir_ajuste_y_validacion(
    tabla: pd.DataFrame,
    entrenamiento: np.ndarray,
    corridas_entrenamiento: list[str],
    corridas_validacion: list[str] | None,
    config: ConfiguracionPreventiva,
) -> DivisionAjuste:
    """P-3. Con `corridas_validacion`, esas corridas salen del ajuste y solo
    eligen el umbral (las de ajuste a menos de H de una de validación también se
    descartan, como en la partición de prueba). Sin ellas (pocas corridas), el
    umbral se elige dejando una corrida fuera sobre las de entrenamiento."""
    if not corridas_validacion:
        return DivisionAjuste(
            metodo=METODO_UMBRAL_DEJAR_UNA_FUERA,
            ajuste=entrenamiento,
            corridas_ajuste=list(corridas_entrenamiento),
            corridas_validacion=list(corridas_entrenamiento),
            validacion=entrenamiento,
        )
    validacion = sorted(set(corridas_validacion))
    ajenas = [c for c in validacion if c not in corridas_entrenamiento]
    if ajenas:
        raise ValueError(
            f"Corridas de validación que no son de entrenamiento (inexistentes, de prueba o descartadas "
            f"por estar a menos de H de una de prueba): {ajenas}"
        )
    sub = tabla.iloc[entrenamiento].reset_index(drop=True)
    division = dividir_por_corrida(sub, validacion, config)
    return DivisionAjuste(
        metodo=METODO_UMBRAL_PARTICION,
        ajuste=entrenamiento[division.entrenamiento],
        corridas_ajuste=division.corridas_entrenamiento,
        corridas_validacion=division.corridas_prueba,
        validacion=entrenamiento[division.prueba],
        corridas_descartadas={
            corrida: motivo.replace("corrida de prueba", "corrida de validación")
            for corrida, motivo in division.corridas_descartadas.items()
        },
        separacion_validacion_min=division.separacion_min,
    )


@dataclass(slots=True)
class SeleccionUmbral:
    """Resultado de P-3. `indices` son las filas cuyas puntuaciones eligieron el
    umbral; `modelo` es el ajustado con las corridas de ajuste (el que se
    evalúa en prueba y se registra)."""

    metodo: str
    umbral: float | None
    indices: np.ndarray
    puntuaciones: np.ndarray
    detalle: dict
    modelo: object = None

    def como_dict(self) -> dict:
        return {"metodo": self.metodo, "umbral": self.umbral, "origen": DESCRIPCION_METODO_UMBRAL[self.metodo], **self.detalle}


def seleccionar_umbral(
    x: np.ndarray, y: np.ndarray, tabla: pd.DataFrame, division: DivisionAjuste, config: ConfiguracionPreventiva
) -> SeleccionUmbral:
    """P-3: el umbral sale del F1 máximo sobre puntuaciones de filas que el
    modelo que las puntúa NO vio al ajustarse, separadas por corrida.

    - Partición de validación: un modelo con las corridas de ajuste puntúa las
      de validación; ese mismo modelo es el que se registra.
    - Dejar una corrida fuera (pocas corridas): por cada corrida de ajuste, un
      modelo sin ella (ni las que quedan a menos de H) la puntúa. El modelo que
      se registra se ajusta después con todas las corridas de ajuste."""
    ajuste = division.ajuste
    if division.metodo == METODO_UMBRAL_PARTICION:
        modelo = _ajustar(x[ajuste], y[ajuste])
        indices = division.validacion
        puntuaciones = modelo.predict_proba(x[indices])[:, 1] if indices.size else np.array([])
        pliegues = None
    else:
        sub = tabla.iloc[ajuste].reset_index(drop=True)
        oof = np.full(ajuste.size, np.nan)
        pliegues = []
        for corrida in division.corridas_ajuste:
            pliegue = dividir_por_corrida(sub, [corrida], config)
            tr, va = pliegue.entrenamiento, pliegue.prueba
            info = {
                "corrida_validada": corrida,
                "corridas_ajuste": pliegue.corridas_entrenamiento,
                "observaciones": int(va.size),
                "positivas": int(y[ajuste[va]].sum()),
            }
            if va.size == 0 or tr.size == 0 or not _ambas_clases(y[ajuste[tr]]):
                pliegues.append({**info, "puntuada": False, "motivo": "el resto de las corridas no tiene ambas clases"})
                continue
            modelo_pliegue = _ajustar(x[ajuste[tr]], y[ajuste[tr]])
            oof[va] = modelo_pliegue.predict_proba(x[ajuste[va]])[:, 1]
            pliegues.append({**info, "puntuada": True})
        puntuadas = np.isfinite(oof)
        indices, puntuaciones = ajuste[puntuadas], oof[puntuadas]
        modelo = _ajustar(x[ajuste], y[ajuste])
    eleccion = (
        elegir_umbral_f1(y[indices], puntuaciones)
        if indices.size
        else {"umbral": None, "f1": None, "motivo": "ninguna corrida pudo puntuarse fuera de muestra"}
    )
    detalle = {
        "corridas_validacion": division.corridas_validacion,
        "observaciones": int(indices.size),
        "positivas": int(y[indices].sum()) if indices.size else 0,
        **{k: v for k, v in eleccion.items() if k != "umbral"},
    }
    if pliegues is not None:
        detalle["pliegues"] = pliegues
    return SeleccionUmbral(division.metodo, eleccion["umbral"], indices, puntuaciones, detalle, modelo)


def evaluar_conjunto(
    rejillas,
    episodios,
    config: ConfiguracionPreventiva,
    conjunto: str,
    corridas_prueba: list[str],
    corridas_validacion: list[str] | None = None,
):
    etiquetadas = etiquetar(rejillas, episodios, config)
    obs = construir_observaciones(etiquetadas, config, conjunto)
    obs.tabla["corrida_id"] = obs.tabla["serie_id"].str.split("#", n=1).str[1]
    particion = dividir_por_corrida(obs.tabla, corridas_prueba, config)
    division = dividir_ajuste_y_validacion(
        obs.tabla, particion.entrenamiento, particion.corridas_entrenamiento, corridas_validacion, config
    )
    entrenamiento, prueba = division.ajuste, particion.prueba
    y = obs.y
    particiones = {
        **particion.como_dict(),
        # Corridas con las que se ajusta el modelo que se evalúa (y se registra).
        "entrenamiento": division.corridas_ajuste,
        "validacion": division.corridas_validacion,
        "metodo_umbral": division.metodo,
        "descartadas": {**particion.corridas_descartadas, **division.corridas_descartadas},
        "separacion_validacion_min": division.separacion_validacion_min,
        "observaciones_entrenamiento": int(entrenamiento.size),
        "observaciones_validacion": int(division.validacion.size),
    }
    base = {
        "conjunto": conjunto,
        "observaciones": len(obs.tabla),
        "observaciones_excluidas": obs.excluidas,
        "particiones": particiones,
    }
    if entrenamiento.size == 0 or prueba.size == 0 or not _ambas_clases(y[entrenamiento]):
        return {**base, "resultado": "no estimable: entrenamiento sin ambas clases o partición vacía"}
    if len(division.corridas_ajuste) < MIN_CORRIDAS_AJUSTE:
        return {
            **base,
            "resultado": f"no estimable: hacen falta al menos {MIN_CORRIDAS_AJUSTE} corridas de ajuste "
            f"para elegir el umbral fuera de muestra (hay {len(division.corridas_ajuste)})",
        }

    seleccion = seleccionar_umbral(obs.x, y, obs.tabla, division, config)
    base["seleccion_umbral"] = seleccion.como_dict()
    if seleccion.umbral is None:
        return {
            **base,
            "resultado": "no estimable: sin umbral fuera de muestra ("
            + str(seleccion.detalle.get("motivo") or "validación sin ambas clases") + ")",
        }
    modelo, umbral = seleccion.modelo, seleccion.umbral
    pendiente = puntuacion_tendencia(obs, HORIZONTE_PENDIENTE_MIN, config)
    # La regla de pendiente no se ajusta: su umbral sale de las MISMAS filas
    # que el del Random Forest.
    umbral_pendiente = elegir_umbral_f1(y[seleccion.indices], pendiente[seleccion.indices])["umbral"]
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
        "positivas_entrenamiento": int(y[entrenamiento].sum()),
        "positivas_prueba": int(y[prueba].sum()),
        "metricas_registro": metricas,
        "criterios": evaluar_criterios_modelo(metricas),
        # Privados (no van al JSON): el modelo evaluado es el que se registra.
        "_modelo": modelo,
        "_nombres": obs.nombres,
    }


def ejecutar_pipeline_corridas(
    ruta_csv: Path,
    corridas_prueba: list[str],
    config: ConfiguracionPreventiva | None = None,
    corridas_validacion: list[str] | None = None,
    conjuntos: tuple[str, ...] = CONJUNTOS_INFORME,
) -> dict:
    config = config or ConfiguracionPreventiva()
    crudas, informe = ingesta.importar_coldcare_csv(ruta_csv)
    lecturas, limpieza = calidad.limpiar(crudas)
    rejillas = calidad.preparar_rejillas(lecturas, config)
    episodios = eventos.agrupar_incidentes(eventos.detectar_episodios(rejillas, config), config)
    resultados = {
        conjunto: evaluar_conjunto(rejillas, episodios, config, conjunto, corridas_prueba, corridas_validacion)
        for conjunto in dict.fromkeys((*CONJUNTOS_INFORME, *conjuntos))
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
        # Privado: lecturas ya limpias (procedencia por corrida al registrar).
        "_lecturas": lecturas,
    }
