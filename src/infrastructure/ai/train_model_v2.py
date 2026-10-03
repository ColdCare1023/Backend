"""Entrenamiento v2 del clasificador de riesgo térmico — corrige la circularidad
diagnosticada en el v1 (ver audit-output/backend/ai/ai_diagnosis_v2.md).

Diagnóstico: en v1, 3 de las 10 features (duracion_fuera_rango,
frecuencia_desviaciones, tendencia_termica) llegaban al modelo IDÉNTICAS a los
valores que la regla determinista usó para generar la etiqueta — sin ninguna
fuente de incertidumbre, a diferencia de la temperatura (que sí llevaba ruido
de sensor). Esto no es fuga train/test clásica (no hay duplicados ni
información futura): es CIRCULARIDAD entre las reglas de etiquetado y las
variables predictoras, que infla la evaluación al medir principalmente la
capacidad del modelo para reproducir la regla, no para estimarla bajo
incertidumbre real.

Corrección v2: en vez de generar duracion_fuera_rango/frecuencia_desviaciones/
tendencia_termica como escalares independientes, se simulan ESCENARIOS
TEMPORALES (episodios de 15-35 lecturas consecutivas, análogos a la ventana de
20 lecturas que usa ConsultarHistorialTermicoUseCase en producción). Para cada
tick del episodio se mantienen dos series paralelas:

  - serie VERDADERA (sin ruido): usada solo para calcular la ETIQUETA, vía la
    misma regla determinista (clasificar_por_regla) aplicada sobre features
    derivadas de la serie verdadera con la MISMA función que usa producción
    (ClasificarRiesgoTermicoUseCase._construir_features).
  - serie OBSERVADA (con ruido de sensor SHT31/DS18B20 + pérdida aleatoria de
    mensajes, simulando reenvíos MQTT perdidos): usada para calcular las
    FEATURES que ve el modelo, con la misma función de producción aplicada
    sobre la serie observada (con gaps).

Esto reproduce fielmente la incertidumbre real de duracion_fuera_rango,
frecuencia_desviaciones y tendencia_termica en producción (donde se calculan
sobre un historial con ruido de sensor y posibles lecturas perdidas), en vez
de asumir que esas tres variables se conocen con certeza absoluta.

Partición: GroupShuffleSplit / StratifiedGroupKFold agrupando por
escenario_id — ningún escenario queda repartido entre train y test.

Cumplimiento RNF-04: el entrenamiento FALLA (no reemplaza el artefacto
oficial) si el F1 ponderado sobre el conjunto de prueba no alcanza 0.85.

Uso: python -m src.infrastructure.ai.train_model_v2
"""

import hashlib
import json
import platform
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import GroupShuffleSplit, StratifiedGroupKFold, cross_val_score

from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.domain.entities.lectura_termica import LecturaTermica
from src.infrastructure.ai.features import FEATURE_NAMES
from src.infrastructure.ai.reglas_riesgo import clasificar_por_regla

MODELS_DIR = Path(__file__).parent / "models"
MODEL_PATH_V2 = MODELS_DIR / "random_forest_v2_corrected.joblib"
METRICS_PATH_V2 = MODELS_DIR / "training_metrics_v2_corrected.json"

RANDOM_STATE = 42
N_ESCENARIOS = 400
TICKS_MIN, TICKS_MAX = 15, 35
CV_FOLDS = 5
F1_MINIMO_RNF04 = 0.85
MODEL_VERSION = "2.0.0-corrected"

# Ruido de medición (mismos valores físicos que v1, según hojas de datos).
RUIDO_TEMP_INTERNA_C = 0.25  # DS18B20: ±0.5 °C máx
RUIDO_TEMP_AMBIENTE_C = 0.15  # SHT31: ±0.3 °C máx
RUIDO_HUMEDAD_PCT = 1.0  # SHT31: ±2 %HR máx
PROB_MENSAJE_PERDIDO = 0.08  # simula reenvío MQTT perdido / lectura no recibida

REGIMENES = ("estable", "deriva_preventiva", "excursion_critica")
PROB_REGIMEN = (0.45, 0.25, 0.30)

# Extractor de features de producción reutilizado literalmente (single source
# of truth exigida): construye el mismo vector que ve el modelo en producción.
_extractor = ClasificarRiesgoTermicoUseCase(ai_service=None)  # type: ignore[arg-type]


def _simular_temperatura_verdadera(regimen: str, n_ticks: int, rng: np.random.Generator) -> np.ndarray:
    temp = np.empty(n_ticks)
    temp[0] = rng.normal(5.0, 0.6)
    if regimen == "estable":
        drift = 0.0
    elif regimen == "deriva_preventiva":
        drift = rng.choice([-1, 1]) * rng.uniform(0.08, 0.16)
    else:  # excursion_critica
        drift = rng.choice([-1, 1]) * rng.uniform(0.35, 0.55)
    for i in range(1, n_ticks):
        temp[i] = temp[i - 1] + drift + rng.normal(0.0, 0.12)
    return temp


def _generar_episodio(escenario_id: str, rng: np.random.Generator) -> list[dict]:
    regimen = rng.choice(REGIMENES, p=PROB_REGIMEN)
    n_ticks = int(rng.integers(TICKS_MIN, TICKS_MAX + 1))
    temp_verdadera = _simular_temperatura_verdadera(regimen, n_ticks, rng)

    ahora = datetime.now(tz=timezone.utc)
    filas: list[dict] = []
    historial_verdadero: list[LecturaTermica] = []
    historial_observado: list[LecturaTermica] = []

    for i in range(n_ticks):
        ts = ahora - timedelta(minutes=(n_ticks - i))
        humedad_verdadera = float(np.clip(rng.normal(55.0, 15.0), 0.0, 100.0))
        temp_ambiental_verdadera = temp_verdadera[i] + rng.normal(2.0, 1.5)
        apertura = bool(rng.random() < (0.25 if regimen == "excursion_critica" else 0.1))

        lectura_verdadera = LecturaTermica(
            device_id=escenario_id,
            timestamp=ts,
            temperatura_ambiental=temp_ambiental_verdadera,
            humedad_ambiental=humedad_verdadera,
            temperatura_interna=float(temp_verdadera[i]),
            apertura_refrigerador=apertura,
            estado_conectividad="online",
        )

        # Serie OBSERVADA: ruido de sensor + posible mensaje perdido (no se
        # agrega al historial observado si "se pierde", simulando un reenvío
        # MQTT fallido — esto es lo que hace que duracion_fuera_rango,
        # frecuencia_desviaciones y tendencia_termica calculadas sobre esta
        # serie difieran genuinamente de las calculadas sobre la verdadera).
        mensaje_perdido = rng.random() < PROB_MENSAJE_PERDIDO
        temp_interna_medida = float(temp_verdadera[i] + rng.normal(0.0, RUIDO_TEMP_INTERNA_C))
        temp_ambiental_medida = float(temp_ambiental_verdadera + rng.normal(0.0, RUIDO_TEMP_AMBIENTE_C))
        humedad_medida = float(np.clip(humedad_verdadera + rng.normal(0.0, RUIDO_HUMEDAD_PCT), 0.0, 100.0))

        lectura_observada = LecturaTermica(
            device_id=escenario_id,
            timestamp=ts,
            temperatura_ambiental=temp_ambiental_medida,
            humedad_ambiental=humedad_medida,
            temperatura_interna=temp_interna_medida,
            apertura_refrigerador=apertura,
            estado_conectividad="offline" if mensaje_perdido else "online",
        )

        # Etiqueta: features de producción sobre la serie VERDADERA (sin ruido).
        features_verdaderas = _extractor._construir_features(lectura_verdadera, historial_verdadero)
        etiqueta = clasificar_por_regla(features_verdaderas)

        # Feature de entrenamiento: features de producción sobre la serie
        # OBSERVADA (con ruido y gaps por mensajes perdidos) — misma función,
        # igual que en inferencia real.
        features_observadas = _extractor._construir_features(lectura_observada, historial_observado)

        filas.append({"vector": features_observadas.to_array(), "etiqueta": etiqueta.value, "escenario_id": escenario_id})

        historial_verdadero.append(lectura_verdadera)
        if not mensaje_perdido:
            historial_observado.append(lectura_observada)

    return filas


def generar_dataset_v2(n_escenarios: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    todas_filas: list[dict] = []
    for _ in range(n_escenarios):
        escenario_id = uuid4().hex[:12]
        todas_filas.extend(_generar_episodio(escenario_id, rng))

    x = np.array([f["vector"] for f in todas_filas])
    y = np.array([f["etiqueta"] for f in todas_filas])
    grupos = np.array([f["escenario_id"] for f in todas_filas])
    return x, y, grupos


def _hash_array(arr: np.ndarray) -> str:
    return hashlib.sha256(arr.tobytes()).hexdigest()


def entrenar() -> dict:
    rng = np.random.default_rng(RANDOM_STATE)
    x, y, grupos = generar_dataset_v2(N_ESCENARIOS, rng)

    splitter = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=RANDOM_STATE)
    train_idx, test_idx = next(splitter.split(x, y, groups=grupos))
    x_train, x_test = x[train_idx], x[test_idx]
    y_train, y_test = y[train_idx], y[test_idx]
    grupos_train = grupos[train_idx]

    # Ningún escenario debe aparecer en ambos conjuntos.
    assert set(grupos[train_idx]).isdisjoint(set(grupos[test_idx])), (
        "Fuga de partición: un escenario quedó repartido entre train y test."
    )

    modelo = RandomForestClassifier(
        n_estimators=200,
        max_depth=12,
        min_samples_leaf=3,
        class_weight="balanced",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )

    cv = StratifiedGroupKFold(n_splits=CV_FOLDS, shuffle=True, random_state=RANDOM_STATE)
    cv_scores = cross_val_score(
        modelo, x_train, y_train, cv=cv, scoring="f1_weighted", groups=grupos_train, n_jobs=-1
    )

    modelo.fit(x_train, y_train)
    y_pred = modelo.predict(x_test)

    exactitud = float(accuracy_score(y_test, y_pred))
    reporte = classification_report(y_test, y_pred, output_dict=True, zero_division=0)
    matriz = confusion_matrix(y_test, y_pred, labels=modelo.classes_).tolist()
    importancias = dict(zip(FEATURE_NAMES, modelo.feature_importances_.tolist(), strict=True))
    f1_ponderado = float(reporte["weighted avg"]["f1-score"])

    dataset_hash = _hash_array(x) + ":" + _hash_array(y)

    metadata = {
        "model_name": "random_forest_thermal_risk",
        "model_version": MODEL_VERSION,
        "trained_at": datetime.now(tz=timezone.utc).isoformat(),
        "sklearn_version": sklearn.__version__,
        "python_version": platform.python_version(),
        "random_state": RANDOM_STATE,
        "n_escenarios": N_ESCENARIOS,
        "n_samples": len(x),
        "n_samples_train": len(x_train),
        "n_samples_test": len(x_test),
        "feature_names": list(FEATURE_NAMES),
        "classes": ["normal", "riesgo_preventivo", "excursion_critica"],
        "particion": "GroupShuffleSplit + StratifiedGroupKFold, agrupado por escenario_id",
        "correccion_respecto_v1": (
            "duracion_fuera_rango, frecuencia_desviaciones y tendencia_termica ya no son "
            "escalares independientes sin incertidumbre: se derivan de una serie temporal "
            "observada con ruido de sensor y pérdida simulada de mensajes MQTT, usando la "
            "misma función de producción (ClasificarRiesgoTermicoUseCase._construir_features)."
        ),
        "ruido_sensores": {
            "temperatura_interna_std_c": RUIDO_TEMP_INTERNA_C,
            "temperatura_ambiental_std_c": RUIDO_TEMP_AMBIENTE_C,
            "humedad_std_pct": RUIDO_HUMEDAD_PCT,
            "prob_mensaje_perdido": PROB_MENSAJE_PERDIDO,
        },
        "dataset_hash": dataset_hash,
    }

    metricas = {
        "metadata": metadata,
        "classes": modelo.classes_.tolist(),
        "accuracy": exactitud,
        "classification_report": reporte,
        "confusion_matrix": matriz,
        "cross_validation": {
            "folds": CV_FOLDS,
            "scoring": "f1_weighted",
            "grouped_by": "escenario_id",
            "scores": cv_scores.tolist(),
            "mean": float(cv_scores.mean()),
            "std": float(cv_scores.std()),
        },
        "feature_importances": importancias,
        "f1_weighted": f1_ponderado,
        "rnf04": {"umbral": F1_MINIMO_RNF04, "cumplido": bool(f1_ponderado >= F1_MINIMO_RNF04)},
    }

    if f1_ponderado < F1_MINIMO_RNF04:
        METRICS_PATH_V2.write_text(json.dumps(metricas, indent=2, ensure_ascii=False), encoding="utf-8")
        raise SystemExit(
            f"RNF-04 NO CUMPLIDO en v2: F1 ponderado {f1_ponderado:.4f} < {F1_MINIMO_RNF04}. "
            f"El resultado real (no manipulado) se guardó en {METRICS_PATH_V2} para análisis. "
            "El artefacto v2 NO se guarda como modelo válido."
        )

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump({"modelo": modelo, "metadata": metadata}, MODEL_PATH_V2)
    model_hash = hashlib.sha256(MODEL_PATH_V2.read_bytes()).hexdigest()
    metadata["model_hash"] = model_hash
    metricas["metadata"] = metadata
    # Regrabar el artefacto con el hash ya embebido en su propia metadata.
    joblib.dump({"modelo": modelo, "metadata": metadata}, MODEL_PATH_V2)

    METRICS_PATH_V2.write_text(json.dumps(metricas, indent=2, ensure_ascii=False), encoding="utf-8")
    return metricas


if __name__ == "__main__":
    resultado = entrenar()
    print(f"Modelo v2 guardado en {MODEL_PATH_V2}")
    print(f"n_samples: {resultado['metadata']['n_samples']} (train={resultado['metadata']['n_samples_train']}, test={resultado['metadata']['n_samples_test']})")
    print(f"Exactitud: {resultado['accuracy']:.4f}")
    print(f"F1 ponderado: {resultado['f1_weighted']:.4f} (RNF-04 >= {F1_MINIMO_RNF04}: {resultado['rnf04']['cumplido']})")
    print(
        f"CV {resultado['cross_validation']['folds']}-fold (agrupado por escenario) f1_weighted: "
        f"{resultado['cross_validation']['mean']:.4f} ± {resultado['cross_validation']['std']:.4f}"
    )
