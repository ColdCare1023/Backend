"""Genera un dataset sintético consistente con la regla térmica base (2 C-8 C),
le inyecta ruido de medición realista y entrena el clasificador Random Forest
de riesgo térmico con validación cruzada estratificada.

El etiquetado proviene de la regla determinista sobre las magnitudes REALES;
las features de entrenamiento llevan el ruido de los sensores (SHT31 ±0.3 °C /
±2 %HR, DS18B20 ±0.5 °C). Así el modelo aprende a recuperar el estado de riesgo
verdadero a partir de mediciones imperfectas — el mismo problema que enfrenta
en producción — y las métricas reportadas no son circulares.

Cumplimiento RNF-04: el entrenamiento FALLA si el F1-score ponderado sobre el
conjunto de pruebas no alcanza 0.85.

Uso: python -m src.infrastructure.ai.train_model
"""

import json
import platform
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split

from src.infrastructure.ai.features import FEATURE_NAMES, FeaturesRiesgoTermico
from src.infrastructure.ai.reglas_riesgo import clasificar_por_regla

MODELS_DIR = Path(__file__).parent / "models"
MODEL_PATH = MODELS_DIR / "random_forest_termico.pkl"
METRICS_PATH = MODELS_DIR / "training_metrics.json"

RANDOM_STATE = 42
N_SAMPLES = 8000
CV_FOLDS = 5
F1_MINIMO_RNF04 = 0.85
MODEL_VERSION = "2.0.0"

# Ruido de medición (desviación estándar) según hojas de datos de los sensores.
RUIDO_TEMP_INTERNA_C = 0.25  # DS18B20: ±0.5 °C máx
RUIDO_TEMP_AMBIENTE_C = 0.15  # SHT31: ±0.3 °C máx
RUIDO_HUMEDAD_PCT = 1.0  # SHT31: ±2 %HR máx


def generar_dataset(n_samples: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    filas: list[list[float]] = []
    etiquetas: list[str] = []

    for _ in range(n_samples):
        # Magnitudes reales del escenario (sin ruido): de aquí sale la etiqueta.
        temperatura_interna = rng.uniform(-5.0, 15.0)
        temperatura_ambiental = temperatura_interna + rng.normal(2.0, 1.5)
        humedad_ambiental = float(np.clip(rng.normal(55.0, 15.0), 0.0, 100.0))
        duracion_fuera_rango = float(np.clip(rng.exponential(8.0), 0.0, 180.0))
        frecuencia_desviaciones = float(rng.poisson(1.2))
        tendencia_termica = float(rng.normal(0.0, 1.2))
        apertura_refrigerador = bool(rng.random() < 0.15)
        hora_evento = int(rng.integers(0, 24))
        estado_conectividad_online = bool(rng.random() < 0.92)

        reales = FeaturesRiesgoTermico(
            temperatura_ambiental=temperatura_ambiental,
            humedad_ambiental=humedad_ambiental,
            temperatura_interna=temperatura_interna,
            diferencia_sensores=temperatura_ambiental - temperatura_interna,
            duracion_fuera_rango=duracion_fuera_rango,
            frecuencia_desviaciones=frecuencia_desviaciones,
            tendencia_termica=tendencia_termica,
            apertura_refrigerador=apertura_refrigerador,
            hora_evento=hora_evento,
            estado_conectividad_online=estado_conectividad_online,
        )
        etiqueta = clasificar_por_regla(reales)

        # Lo que el modelo VE: las magnitudes medidas por sensores con ruido.
        temp_interna_medida = temperatura_interna + rng.normal(0.0, RUIDO_TEMP_INTERNA_C)
        temp_ambiente_medida = temperatura_ambiental + rng.normal(0.0, RUIDO_TEMP_AMBIENTE_C)
        humedad_medida = float(
            np.clip(humedad_ambiental + rng.normal(0.0, RUIDO_HUMEDAD_PCT), 0.0, 100.0)
        )
        medidas = FeaturesRiesgoTermico(
            temperatura_ambiental=temp_ambiente_medida,
            humedad_ambiental=humedad_medida,
            temperatura_interna=temp_interna_medida,
            diferencia_sensores=temp_ambiente_medida - temp_interna_medida,
            duracion_fuera_rango=duracion_fuera_rango,
            frecuencia_desviaciones=frecuencia_desviaciones,
            tendencia_termica=tendencia_termica,
            apertura_refrigerador=apertura_refrigerador,
            hora_evento=hora_evento,
            estado_conectividad_online=estado_conectividad_online,
        )

        filas.append(medidas.to_array())
        etiquetas.append(etiqueta.value)

    return np.array(filas), np.array(etiquetas)


def entrenar() -> dict:
    rng = np.random.default_rng(RANDOM_STATE)
    x, y = generar_dataset(N_SAMPLES, rng)
    x_train, x_test, y_train, y_test = train_test_split(
        x, y, test_size=0.2, random_state=RANDOM_STATE, stratify=y
    )

    modelo = RandomForestClassifier(
        n_estimators=200,
        max_depth=12,
        min_samples_leaf=3,
        class_weight="balanced",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )

    # Validación cruzada estratificada sobre el conjunto de entrenamiento:
    # estima la estabilidad del modelo antes de tocar el conjunto de pruebas.
    cv = StratifiedKFold(n_splits=CV_FOLDS, shuffle=True, random_state=RANDOM_STATE)
    cv_scores = cross_val_score(modelo, x_train, y_train, cv=cv, scoring="f1_weighted", n_jobs=-1)

    modelo.fit(x_train, y_train)
    y_pred = modelo.predict(x_test)

    exactitud = float(accuracy_score(y_test, y_pred))
    reporte = classification_report(y_test, y_pred, output_dict=True, zero_division=0)
    matriz = confusion_matrix(y_test, y_pred, labels=modelo.classes_).tolist()
    importancias = dict(zip(FEATURE_NAMES, modelo.feature_importances_.tolist(), strict=True))
    f1_ponderado = float(reporte["weighted avg"]["f1-score"])

    if f1_ponderado < F1_MINIMO_RNF04:
        raise SystemExit(
            f"RNF-04 NO CUMPLIDO: F1 ponderado {f1_ponderado:.4f} < {F1_MINIMO_RNF04}. "
            "El modelo no se guarda."
        )

    metadata = {
        "model_version": MODEL_VERSION,
        "trained_at": datetime.now(tz=timezone.utc).isoformat(),
        "sklearn_version": sklearn.__version__,
        "python_version": platform.python_version(),
        "random_state": RANDOM_STATE,
        "n_samples": N_SAMPLES,
        "feature_names": list(FEATURE_NAMES),
        "ruido_sensores": {
            "temperatura_interna_std_c": RUIDO_TEMP_INTERNA_C,
            "temperatura_ambiental_std_c": RUIDO_TEMP_AMBIENTE_C,
            "humedad_std_pct": RUIDO_HUMEDAD_PCT,
        },
    }

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump({"modelo": modelo, "metadata": metadata}, MODEL_PATH)

    metricas = {
        "metadata": metadata,
        "classes": modelo.classes_.tolist(),
        "accuracy": exactitud,
        "classification_report": reporte,
        "confusion_matrix": matriz,
        "cross_validation": {
            "folds": CV_FOLDS,
            "scoring": "f1_weighted",
            "scores": cv_scores.tolist(),
            "mean": float(cv_scores.mean()),
            "std": float(cv_scores.std()),
        },
        "feature_importances": importancias,
        "f1_weighted": f1_ponderado,
        "rnf04": {"umbral": F1_MINIMO_RNF04, "cumplido": True},
    }
    METRICS_PATH.write_text(json.dumps(metricas, indent=2, ensure_ascii=False), encoding="utf-8")
    return metricas


if __name__ == "__main__":
    resultado = entrenar()
    print(f"Modelo guardado en {MODEL_PATH}")
    print(f"Exactitud: {resultado['accuracy']:.4f}")
    print(f"F1 ponderado: {resultado['f1_weighted']:.4f} (RNF-04 >= {F1_MINIMO_RNF04}: cumplido)")
    print(
        f"CV {resultado['cross_validation']['folds']}-fold f1_weighted: "
        f"{resultado['cross_validation']['mean']:.4f} ± {resultado['cross_validation']['std']:.4f}"
    )
