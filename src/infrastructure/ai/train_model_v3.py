"""Entrenamiento v3 — corrige AIV-01 (reproducibilidad) y AIV-06 (circularidad
de model_hash) detectados en la re-verificación independiente de la serie v2.

AIV-01: en v2, `escenario_id = uuid4().hex[:12]` no dependía del `rng` sembrado,
por lo que dos ejecuciones con el mismo `random_state` producían `x`/`y`
byte-idénticos pero agrupaciones (`escenario_id`) distintas, y por lo tanto
particiones train/test y métricas ligeramente distintas. v3 deriva
`escenario_id` determinísticamente de la semilla y el índice de escenario
(`f"esc-{RANDOM_STATE:04d}-{indice:05d}"`), sin ninguna fuente de entropía del
sistema operativo.

AIV-06: en v2, el artefacto se serializaba dos veces (una para poder calcular
su propio hash, otra para reincorporar ese hash en su propia metadata
embebida) — circularidad de auto-referencia inevitable con ese diseño. v3
cambia la estructura: el modelo se guarda UNA sola vez como estimador puro
(`random_forest_termico.pkl`, sin dict envolvente), se calcula su SHA-256
sobre ese archivo definitivo, y ese hash se escribe en un archivo de metadata
EXTERNO (`model_metadata.json`) que nunca vuelve a tocar el .pkl.

Nota de precisión (mandato explícito: no confundir reproducibilidad funcional
con identidad binaria del pickle): el dataset (x, y, grupos, partición,
métricas, matriz de confusión) es reproducible bit a bit entre ejecuciones con
la misma configuración. El archivo .pkl en sí puede NO ser byte-idéntico entre
ejecuciones (joblib/pickle pueden variar en metadatos internos de protocolo,
y RandomForestClassifier con n_jobs>1 paralelizado puede acumular en distinto
orden en punto flotante en la construcción interna de árboles, aunque el
resultado de `predict`/`predict_proba` sea idéntico en la práctica para este
dataset). Esto se verifica y documenta explícitamente en
`02_reproducibility_fix.md`, no se asume.

Uso: python -m src.infrastructure.ai.train_model_v3
"""

import hashlib
import json
import platform
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
from sklearn.model_selection import GroupShuffleSplit, StratifiedGroupKFold, cross_val_score
from sklearn.tree import DecisionTreeClassifier

from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.domain.entities.lectura_termica import LecturaTermica
from src.infrastructure.ai.features import FEATURE_NAMES
from src.infrastructure.ai.reglas_riesgo import clasificar_por_regla

MODELS_DIR = Path(__file__).parent / "models"
MODEL_PATH_V3 = MODELS_DIR / "random_forest_v3_reproducible.joblib"
METADATA_PATH_V3 = MODELS_DIR / "model_metadata_v3.json"
METRICS_PATH_V3 = MODELS_DIR / "training_metrics_v3_reproducible.json"
OFFICIAL_MODEL_PATH = MODELS_DIR / "random_forest_termico.pkl"
OFFICIAL_METADATA_PATH = MODELS_DIR / "model_metadata.json"
OFFICIAL_METRICS_PATH = MODELS_DIR / "training_metrics.json"

RANDOM_STATE = 42
N_ESCENARIOS = 400
TICKS_MIN, TICKS_MAX = 15, 35
CV_FOLDS = 5
F1_MINIMO_RNF04 = 0.85
# 4.0.0: HU-16/17/18 (backlog 54 HU) — clasificador binario
# (normal/riesgo_preventivo). excursion_critica deja de ser una clase del
# modelo: pasa a ser exclusivamente el resultado de la regla determinista de
# rango 2-8 °C, fuera de este pipeline (ver reglas_riesgo.clasificar_por_regla
# y RandomForestRiesgoService._CLASES_ESPERADAS).
MODEL_VERSION = "4.0.0-binario"

RUIDO_TEMP_INTERNA_C = 0.25
RUIDO_TEMP_AMBIENTE_C = 0.15
RUIDO_HUMEDAD_PCT = 1.0
PROB_MENSAJE_PERDIDO = 0.08

REGIMENES = ("estable", "deriva_preventiva", "excursion_critica")
PROB_REGIMEN = (0.45, 0.25, 0.30)

_extractor = ClasificarRiesgoTermicoUseCase(ai_service=None)  # type: ignore[arg-type]


def _simular_temperatura_verdadera(regimen: str, n_ticks: int, rng: np.random.Generator) -> np.ndarray:
    temp = np.empty(n_ticks)
    temp[0] = rng.normal(5.0, 0.6)
    if regimen == "estable":
        drift = 0.0
    elif regimen == "deriva_preventiva":
        drift = rng.choice([-1, 1]) * rng.uniform(0.08, 0.16)
    else:
        drift = rng.choice([-1, 1]) * rng.uniform(0.35, 0.55)
    for i in range(1, n_ticks):
        temp[i] = temp[i - 1] + drift + rng.normal(0.0, 0.12)
    return temp


def _generar_episodio(
    escenario_id: str, regimen: str, n_ticks: int, rng: np.random.Generator
) -> list[dict]:
    temp_verdadera = _simular_temperatura_verdadera(regimen, n_ticks, rng)

    # Nunca usar reloj del sistema en generación. Aunque sólo se extraiga la
    # hora como feature, `datetime.now()` cambiaba el dataset entre ejecuciones
    # realizadas en horas distintas con idéntica configuración.
    ahora = datetime(2026, 1, 1, tzinfo=timezone.utc)
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

        features_verdaderas = _extractor._construir_features(lectura_verdadera, historial_verdadero)
        etiqueta = clasificar_por_regla(features_verdaderas)
        features_observadas = _extractor._construir_features(lectura_observada, historial_observado)

        filas.append({"vector": features_observadas.to_array(), "etiqueta": etiqueta.value, "escenario_id": escenario_id})

        historial_verdadero.append(lectura_verdadera)
        if not mensaje_perdido:
            historial_observado.append(lectura_observada)

    return filas


def generar_dataset_v3(n_escenarios: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Idéntico a v2 salvo por AIV-01: escenario_id determinista (semilla +
    índice), nunca uuid4()."""
    todas_filas: list[dict] = []
    for indice in range(n_escenarios):
        regimen = str(rng.choice(REGIMENES, p=PROB_REGIMEN))
        n_ticks = int(rng.integers(TICKS_MIN, TICKS_MAX + 1))
        # Identificador reproducible y explicable: semilla + índice + tipo de
        # episodio + parámetro que define su longitud. No UUID, reloj ni azar
        # externo.
        escenario_id = f"esc-{RANDOM_STATE:04d}-{indice:05d}-{regimen}-{n_ticks:02d}"
        todas_filas.extend(_generar_episodio(escenario_id, regimen, n_ticks, rng))

    x = np.array([f["vector"] for f in todas_filas])
    y = np.array([f["etiqueta"] for f in todas_filas])
    grupos = np.array([f["escenario_id"] for f in todas_filas])
    return x, y, grupos


def _hash_array(arr: np.ndarray) -> str:
    return hashlib.sha256(arr.tobytes()).hexdigest()


def entrenar() -> dict:
    rng = np.random.default_rng(RANDOM_STATE)
    x, y, grupos = generar_dataset_v3(N_ESCENARIOS, rng)
    dataset_hash = _hash_array(x) + ":" + _hash_array(y)

    splitter = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=RANDOM_STATE)
    train_idx, test_idx = next(splitter.split(x, y, groups=grupos))
    x_train, x_test = x[train_idx], x[test_idx]
    y_train, y_test = y[train_idx], y[test_idx]
    grupos_train = grupos[train_idx]

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

    # Líneas base de comparación obligatorias (mismo train/test que el modelo).
    baseline_mayoritario = DummyClassifier(strategy="most_frequent")
    baseline_mayoritario.fit(x_train, y_train)
    f1_mayoritario = float(
        f1_score(y_test, baseline_mayoritario.predict(x_test), average="weighted", zero_division=0)
    )

    arbol_simple = DecisionTreeClassifier(max_depth=6, random_state=RANDOM_STATE, class_weight="balanced")
    arbol_simple.fit(x_train, y_train)
    f1_arbol = float(f1_score(y_test, arbol_simple.predict(x_test), average="weighted", zero_division=0))

    if f1_ponderado < F1_MINIMO_RNF04:
        raise SystemExit(
            f"RNF-04 NO CUMPLIDO en v3: F1 ponderado {f1_ponderado:.4f} < {F1_MINIMO_RNF04}. "
            "El artefacto v3 NO se guarda como modelo válido (resultado real, no manipulado)."
        )

    # --- AIV-06: una sola escritura del artefacto, hash calculado DESPUÉS, sin
    # re-serializar. El modelo se guarda como estimador puro (sin dict
    # envolvente con metadata embebida) para eliminar la auto-referencia.
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(modelo, MODEL_PATH_V3)
    model_hash = hashlib.sha256(MODEL_PATH_V3.read_bytes()).hexdigest()

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
        "classes": modelo.classes_.tolist(),
        "hyperparameters": modelo.get_params(),
        "particion": "GroupShuffleSplit + StratifiedGroupKFold, agrupado por escenario_id determinista (AIV-01)",
        "correccion_respecto_v2": (
            "escenario_id ya no usa uuid4() (entropía del SO); se deriva de "
            "f'esc-{random_state:04d}-{indice:05d}', determinista y reproducible."
        ),
        "correccion_hash_respecto_v2": (
            "model_hash se calcula UNA vez sobre el archivo .pkl ya guardado "
            "definitivamente (estimador puro, sin dict envolvente); el archivo "
            "no se vuelve a serializar tras calcular el hash (corrige AIV-06)."
        ),
        "ruido_sensores": {
            "temperatura_interna_std_c": RUIDO_TEMP_INTERNA_C,
            "temperatura_ambiental_std_c": RUIDO_TEMP_AMBIENTE_C,
            "humedad_std_pct": RUIDO_HUMEDAD_PCT,
            "prob_mensaje_perdido": PROB_MENSAJE_PERDIDO,
        },
        "dataset_hash": dataset_hash,
        "model_hash": model_hash,
        "accuracy": exactitud,
        "f1_weighted": f1_ponderado,
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
        "rnf04": {"umbral": F1_MINIMO_RNF04, "cumplido": bool(f1_ponderado >= F1_MINIMO_RNF04)},
        "baselines": {
            "mayoritario_f1_weighted": f1_mayoritario,
            "arbol_simple_f1_weighted": f1_arbol,
        },
    }

    METADATA_PATH_V3.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    METRICS_PATH_V3.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    # Promoción por copia de bytes, no por una segunda serialización: el hash
    # calculado arriba sigue describiendo exactamente el archivo oficial.
    shutil.copyfile(MODEL_PATH_V3, OFFICIAL_MODEL_PATH)
    shutil.copyfile(METADATA_PATH_V3, OFFICIAL_METADATA_PATH)
    shutil.copyfile(METRICS_PATH_V3, OFFICIAL_METRICS_PATH)
    return metadata


if __name__ == "__main__":
    resultado = entrenar()
    print(f"Modelo v3 guardado en {MODEL_PATH_V3}")
    print(f"Metadata externa en {METADATA_PATH_V3}")
    print(f"model_hash (post-guardado, sin re-serializar): {resultado['model_hash']}")
    print(f"dataset_hash: {resultado['dataset_hash']}")
    print(f"n_samples: {resultado['n_samples']} (train={resultado['n_samples_train']}, test={resultado['n_samples_test']})")
    print(f"Exactitud: {resultado['accuracy']:.4f}")
    print(f"F1 ponderado: {resultado['f1_weighted']:.4f} (RNF-04 >= {F1_MINIMO_RNF04}: {resultado['rnf04']['cumplido']})")
    print(f"Baseline mayoritario F1: {resultado['baselines']['mayoritario_f1_weighted']:.4f}")
    print(f"Baseline árbol simple F1: {resultado['baselines']['arbol_simple_f1_weighted']:.4f}")
