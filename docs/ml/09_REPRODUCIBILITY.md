# 09 — Reproducibilidad

## Entorno

- Python 3.12 (CI) · scikit-learn 1.9.0 · numpy 2.5.4 · pandas 3.0.6 · scipy 1.18.1 · joblib 1.6.0 ·
  matplotlib 3.11.2 (solo offline).
- `requirements-ml.txt`: lockfile con hashes del entorno de entrenamiento (= `requirements-dev.txt` +
  matplotlib). Los lockfiles de producción y de CI **no cambiaron**.
- scikit-learn queda fijado en `requirements.txt` (hoy 1.9.0) y el backend carga los artefactos con esa
  versión. Los artefactos `rf-preventivo-h30-*-1.0.0` de los datasets externos se generaron con 1.9.1
  (`versiones.scikit_learn` de su manifiesto): sirven para reproducir los experimentos A–C, no para
  activarse (P-6). `corridas --registrar` no registra si el scikit-learn instalado no es exactamente
  el de `requirements.txt`.
- Semilla: 42 (RF, búsqueda y bootstrap). La inferencia del artefacto usa `n_jobs=1`, por lo que sus
  puntuaciones son idénticas bit a bit entre ejecuciones.

## Entradas (no versionadas)

Copiar a `data/ml/external_inputs/`: `Raw.zip`, `Preprocessed.zip`, `overview.csv`,
`experiment_actions.csv`, `DataPreprocessing.ipynb` (Zenodo 15130001) y `bio_data_10mindata_4d.csv`
(Zenodo 22101912). El pipeline verifica que existan y registra sus SHA-256 (ver `DATASET_AUDIT.md`).
Si falta alguno, se detiene: no descarga sustitutos.

## Comandos (desde `Backend/`)

Con un entorno local:

```bash
python -m venv .venv-ml && .venv-ml/Scripts/activate
```

```bash
pip install --require-hashes -r requirements-ml.txt
```

```bash
python -m src.infrastructure.ai.prediccion_preventiva auditar
```

```bash
python -m src.infrastructure.ai.prediccion_preventiva entrenar --sin-registro
```

```bash
python -m src.infrastructure.ai.prediccion_preventiva entrenar --version 1.0.1
```

```bash
python -m src.infrastructure.ai.prediccion_preventiva listar
```

```bash
python -m src.infrastructure.ai.prediccion_preventiva verificar --version rf-preventivo-h30-termico-1.0.0
```

```bash
pytest
```

Sin Python local (como en la máquina de desarrollo de esta implementación), con Docker:

```bash
docker run --rm -v "$PWD:/app" -w /app python:3.12-slim bash -c "pip install -q --require-hashes -r requirements-ml.txt && python -m src.infrastructure.ai.prediccion_preventiva entrenar --sin-registro"
```

`entrenar` sin `--sin-registro` falla si la versión ya existe (no sobrescribe). Para comprobar la
reproducibilidad, ejecutar dos veces con `--sin-registro` y comparar
`docs/ml/artefactos/resultados_experimentos.json` (difieren solo `generado_en` y `entrenado_en`).

## Experimento D (datos ColdCare)

1. Activar el modo sombra: `IA_PREVENTIVO_MODO=sombra`,
   `IA_PREVENTIVO_VERSION=rf-preventivo-h30-termico-1.0.0` (o la variante `-puerta-` con MC-38).
2. Exportar la telemetría real con el exportador de corridas (P-1, ver `11_DATOS_DE_PRUEBA_Y_CORRIDAS.md`).
   El periodo en sombra se declara como una corrida por dispositivo en un manifiesto:

```csv
corrida_id,device_id,inicio,fin,tipo,uso
D01,ESP32_FARMACIA_01,2026-12-01T00:00:00Z,2026-12-29T00:00:00Z,S-N,prueba
```

```bash
DATABASE_URL=postgresql+asyncpg://... python -m scripts.exportar_corridas \
    --manifiesto experimento_d_manifiesto.csv --salida lecturas.csv --resumen lecturas_resumen.json
```

   El exportador abre la base en solo lectura y escribe `device_id`, `timestamp` (=
   `COALESCE(timestamp_estimado, timestamp)`), `temperatura_interna`, `apertura_refrigerador`,
   `time_quality`, `ds18b20_status`, `sensor_fault`, `corrida_id`, `temperatura_ambiental`,
   `humedad_ambiental`, `boot_id`, `seq_no` y `origen_hora`. La consulta anterior solo traía las
   cuatro primeras columnas y usaba la hora de llegada de las lecturas reconstruidas. Para una sola
   corrida, esta consulta en `psql` da las mismas 13 columnas y filas. Los formatos son los de
   PostgreSQL (hora según `DateStyle`, booleanos `t`/`f`), así que para el pipeline use el exportador:

```sql
COPY (SELECT device_id,
             COALESCE(timestamp_estimado, timestamp) AS timestamp,
             temperatura_interna, apertura_refrigerador, time_quality,
             CASE WHEN temperatura_interna IS NULL THEN 'SENSOR_FAULT' ELSE 'OK' END AS ds18b20_status,
             payload->>'sensor_fault' AS sensor_fault,
             'D01' AS corrida_id,
             temperatura_ambiental, humedad_ambiental, boot_id, seq_no,
             CASE WHEN time_quality = 'unsynced'
                  THEN CASE WHEN timestamp_estimado IS NULL THEN 'recepcion' ELSE 'reconstruida' END
                  ELSE 'nodo' END AS origen_hora
      FROM thermal_readings
      WHERE origen = 'dispositivo' AND (payload->>'simulated') IS DISTINCT FROM 'true'
        AND device_id = 'ESP32_FARMACIA_01'
        AND COALESCE(timestamp_estimado, timestamp) >= '2026-12-01T00:00:00Z'
        AND COALESCE(timestamp_estimado, timestamp) <  '2026-12-29T00:00:00Z'
      ORDER BY 2, boot_id, seq_no) TO STDOUT WITH CSV HEADER;
```

3. Evaluar:

```bash
python -m src.infrastructure.ai.prediccion_preventiva evaluar-coldcare --csv lecturas.csv --version rf-preventivo-h30-termico-1.0.0 --salida experimento_d.json
```

4. Solo con criterios cumplidos (ver 08), el responsable cambia el estado:

```bash
python -m src.infrastructure.ai.prediccion_preventiva estado --version rf-preventivo-h30-termico-1.0.0 --nuevo APPROVED_FOR_DEPLOYMENT --motivo "Exp. D cumple criterios" --responsable "Nombre" --evidencia experimento_d.json
```

Un modelo aprobado debe registrarse como versión nueva si cambia el artefacto.

## Corridas de banco (HU-17/HU-18)

Cada hoja de corrida se convierte en una fila del manifiesto (formato en
`11_DATOS_DE_PRUEBA_Y_CORRIDAS.md`, sección 2). Luego se exporta del stack local y se entrena y evalúa
con las corridas de prueba y de validación que da el resumen:

```bash
DATABASE_URL=postgresql+asyncpg://... python -m scripts.exportar_corridas \
    --manifiesto hojas_corrida.csv --salida corridas.csv --resumen corridas_resumen.json
python -m src.infrastructure.ai.prediccion_preventiva corridas --csv corridas.csv --prueba <resumen.prueba> \
    --validacion <resumen.validacion> --salida corridas.json
```

Las lecturas de prueba con valores exactos (P-13, `infra-local/scripts/publicador_prueba_mqtt.py`) usan
un `device_id` de prueba y no entran en el manifiesto.

### Umbral de decisión (P-3)

El umbral es el de F1 máximo sobre puntuaciones fuera de la muestra de entrenamiento, separadas por
corrida (`corridas.seleccionar_umbral`):

| Método (`metodo_umbral`) | Cuándo | De dónde salen las puntuaciones | Modelo que se evalúa y registra |
|---|---|---|---|
| `particion_validacion` | Con `--validacion` (hojas con `uso=validacion`) | Las corridas de validación, puntuadas por un modelo ajustado sin ellas | Ese mismo modelo, ajustado solo con las corridas de ajuste |
| `dejar_una_corrida_fuera` | Sin `--validacion`: respaldo con pocas corridas | Cada corrida de entrenamiento, puntuada por un modelo ajustado sin ella | Uno ajustado después con todas las corridas de entrenamiento |

En los dos casos una corrida de ajuste a menos de H de la corrida que se puntúa (mismo equipo) queda
fuera de ese ajuste, como frente a la prueba. Con menos de 2 corridas de ajuste, o sin puntuaciones
fuera de muestra con ambas clases, el conjunto queda "no estimable" (antes se usaba 0,5). La partición
de validación es preferible cuando, al separarla, quedan al menos 2 corridas de ajuste con excursión y
la validación tiene al menos una.

### Registrar el modelo (P-2)

```bash
python -m src.infrastructure.ai.prediccion_preventiva corridas --csv corridas.csv --prueba <resumen.prueba> \
    --validacion <resumen.validacion> --horizonte 30 --registrar --version rf-corridas-h30-termico-1.0.0 \
    --conjunto termico_minimo --salida corridas.json
python -m src.infrastructure.ai.prediccion_preventiva verificar --version rf-corridas-h30-termico-1.0.0
```

Registra el modelo evaluado, sin reentrenarlo, en `src/infrastructure/ai/models/preventivo/<V>/` como
`EXPERIMENTAL`, y anota el artefacto y el manifiesto en `historial.json`. La reproducibilidad queda en
el manifiesto:

- `datos`: nombre, SHA-256 y tamaño del CSV exportado, `dataset_version`, filas leídas y válidas,
  descartes, rango de fechas y cada corrida con su rol (`entrenamiento`, `validacion`, `prueba` o
  `descartada`), su rango y sus lecturas;
- `particiones` y `seleccion_umbral`: las corridas de cada partición, el método del umbral, sus
  pliegues y la separación mínima;
- `configuracion`, `hiperparametros`, `semilla` (42), `versiones` (Python, scikit-learn, numpy,
  pandas, scipy, joblib) y `requisitos.scikit_learn_fijado`.

Con el mismo CSV, las mismas corridas y el mismo entorno, el artefacto (mismo SHA-256) y el umbral
salen iguales: semilla fija y `n_jobs=1` en el ajuste y en la inferencia. Cambian `entrenado_en` y, por eso, el SHA-256 del
manifiesto; el registro nunca sobrescribe, así que la segunda ejecución va con otra versión o con
`--directorio-registro <directorio temporal>`. Las negativas (scikit-learn distinto del fijado, versión
existente, pocas corridas o etiquetas) y los pasos siguientes (sombra, Experimento D, aprobación,
registro en la base y `/activar`) están en `docs/operacion/IA_PREVENTIVA_ACTIVACION.md`, pasos 3 a 9.

## Salidas

| Ruta | Contenido |
|---|---|
| `docs/ml/DATASET_AUDIT.md`, `docs/ml/RESULTADOS_EXPERIMENTOS.md` | Tablas generadas |
| `docs/ml/artefactos/*.json, *.csv, *.png` | Auditoría, resultados, episodios, incidentes, búsqueda y figuras |
| `src/infrastructure/ai/models/preventivo/` | Artefactos, manifiestos e historial del registro |
| `data/ml/work/` (no versionado) | Predicciones de prueba por instante |
