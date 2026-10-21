# 09 — Reproducibilidad

## Entorno

- Python 3.12 (CI) · scikit-learn 1.9.1 · numpy 2.5.4 · pandas 3.0.6 · scipy 1.18.1 · joblib 1.6.0 ·
  matplotlib 3.11.2 (solo offline).
- `requirements-ml.txt`: lockfile con hashes del entorno de entrenamiento (= `requirements-dev.txt` +
  matplotlib). Los lockfiles de producción y de CI **no cambiaron**.
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
2. Exportar la telemetría real:

```sql
COPY (SELECT device_id, timestamp, temperatura_interna, apertura_refrigerador
      FROM thermal_readings
      WHERE origen = 'dispositivo' AND (payload->>'simulated') IS DISTINCT FROM 'true'
      ORDER BY device_id, timestamp) TO STDOUT WITH CSV HEADER;
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

## Salidas

| Ruta | Contenido |
|---|---|
| `docs/ml/DATASET_AUDIT.md`, `docs/ml/RESULTADOS_EXPERIMENTOS.md` | Tablas generadas |
| `docs/ml/artefactos/*.json, *.csv, *.png` | Auditoría, resultados, episodios, incidentes, búsqueda y figuras |
| `src/infrastructure/ai/models/preventivo/` | Artefactos, manifiestos e historial del registro |
| `data/ml/work/` (no versionado) | Predicciones de prueba por instante |
