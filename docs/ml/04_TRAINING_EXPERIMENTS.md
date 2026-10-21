# 04 — Experimentos de entrenamiento

Código: `entrenamiento.py`, `particion.py`, `experimentos.py`. Tablas completas en
[`RESULTADOS_EXPERIMENTOS.md`](RESULTADOS_EXPERIMENTOS.md).

## 1. Diseño fijado antes de entrenar

| Elemento | Decisión |
|---|---|
| Algoritmo | `sklearn.ensemble.RandomForestClassifier` (n_estimators = 300, max_samples = 0,5, semilla 42) |
| Horizonte principal | 30 min (15 min solo como sensibilidad) |
| Desarrollo | 02-09 → 08-09 (hora local), sensores 03, 04, 06 y 07 |
| Validación | Hacia adelante por días, con cortes 05, 06 y 07-09: cada pliegue entrena solo con su pasado |
| Prueba independiente | 09-09 00:00 → 11-09 (5 incidentes; incluye los sensores 05 y 09, ausentes en el desarrollo) |
| Purga y embargo | Se descarta del lado anterior toda observación con t + 30 min ≥ corte y del posterior toda con t − 15 min < corte |
| Selección de hiperparámetros | Average Precision de las predicciones fuera de pliegue |
| Umbral | F1 máximo en validación (nunca en prueba) |
| Modelo final | Reentrenado con todo el desarrollo, con los hiperparámetros y el umbral ya fijados |
| Calibración | No: con 5 incidentes de desarrollo no hay datos independientes para calibrar. La puntuación se trata como **no calibrada** |
| Remuestreo / SMOTE | No: generaría información artificial entre minutos dependientes |

Rejilla deliberadamente pequeña (24 combinaciones): `max_depth` ∈ {4, 8, None},
`min_samples_leaf` ∈ {10, 50}, `max_features` ∈ {sqrt, 0,5}, `class_weight` ∈ {None,
balanced_subsample}. Con 10 incidentes, una búsqueda extensa solo sobreajusta la validación.

**Auditoría automática de fugas** (desarrollo/prueba y cada pliegue): 0 índices duplicados, 0
etiquetas incompletas, 0 ventanas [t−15, t+30] solapadas entre particiones, 0 incidentes
compartidos y 0 transformaciones ajustadas con datos de prueba (el pipeline no tiene escaladores
ni imputadores). Si alguna comprobación falla, el entrenamiento se detiene.

## 2. Baselines (misma información en t, parámetros elegidos en la misma validación)

1. Clase mayoritaria. 2. Proximidad al límite (umbral de distancia). 3. Tendencia reciente
(extrapolación de la pendiente de 10 min; horizonte de extrapolación y umbral elegidos en
validación). 4. **Regla legada de ColdCare** (`reglas_riesgo.clasificar_por_regla`, sin ajuste).

## 3. Experimentos

| Id | Descripción | Estado |
|---|---|---|
| A1 | Hohenheim, `termico_minimo`, H = 30 | Ejecutado (modelo principal) |
| A2 | Hohenheim, `termico_puerta` (puerta del protocolo como sustituto del MC-38), H = 30 | Ejecutado |
| A3 | Hohenheim, `termico_minimo`, H = 15 (sensibilidad) | Ejecutado |
| B | Hospital, entrenamiento y prueba propios | **No viable**: 5 episodios en un equipo y un día |
| C / C2 | A1 / A2 aplicados sin reentrenar a BLD26-001 + MHT2025-001 | Ejecutado |
| D | Datos ESP32/DS18B20 de ColdCare | **Pendiente** (pipeline listo: `evaluar-coldcare`, modo sombra) |

## 4. Hiperparámetros seleccionados

| Exp. | Seleccionados | Umbral (validación) |
|---|---|---|
| A1 | max_depth = None, min_samples_leaf = 50, sqrt, balanced_subsample | 0,749 |
| A2 | max_depth = 4, min_samples_leaf = 10, 0,5, sin pesos | 0,276 |
| A3 | max_depth = 4, min_samples_leaf = 10, 0,5, sin pesos | 0,400 |

[Resultado] AP por pliegue de A1: 0,153 (05-09), 0,205 (06-09) y 0,013 (07-09, fin de semana con
un solo pico nocturno). En validación, A1 (AP 0,101) queda por debajo de la proximidad (0,171) y de
la tendencia (0,167). El primer pliegue entrena con solo 39 positivos.

## 5. Reproducibilidad

Versiones, semilla y SHA-256 de las entradas quedan en cada manifiesto. Dos ejecuciones idénticas
producen las mismas métricas (verificado; ver 09). La inferencia del artefacto usa `n_jobs=1` para
que las puntuaciones sean idénticas bit a bit.
