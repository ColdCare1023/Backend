# 01 — Auditoría de datasets

> Cifras exactas, tablas por sensor y figuras: [`DATASET_AUDIT.md`](DATASET_AUDIT.md) (generado por
> `python -m src.infrastructure.ai.prediccion_preventiva auditar`) y `artefactos/auditoria_datasets.json`.
> Este documento explica las decisiones. Convención: **[Resultado]** = medido; **[Decisión]**;
> **[Limitación]**; **[Hipótesis]**.

## 1. Estado inicial del repositorio (antes de modificar)

- Rama `develop`, árbol limpio, último commit `a43bf27`. Suite: **772 pruebas pasan y 4 dan error de
  entorno** (`test_be007_*`: el PostgreSQL embebido `pgserver` no inicializa en `python:3.12-slim`).
  Ruff limpio.
- **Modelo existente** (`src/infrastructure/ai/`): Random Forest binario `4.0.0-binario` (v3),
  10 features de un solo instante, entrenado con `train_model_v3.py` sobre **400 escenarios
  sintéticos** cuya etiqueta la produce `reglas_riesgo.clasificar_por_regla`, la misma regla que se
  usa como salvaguarda. [Resultado] Su F1 ≥ 0,85 mide cuánto imita el bosque a la regla, **no** su
  capacidad de anticipar: la etiqueta describe el estado actual (no el futuro) y cualquier lectura
  fuera de rango se etiqueta `riesgo_preventivo`. No se eliminó (sigue siendo el clasificador del
  flujo actual), pero `/api/ia/modelo` ahora declara `datos_simulados: true` y
  `estado_evaluacion: "ilustrativa_no_reportable"`, campos que el frontend ya esperaba.

## 2. Archivos de entrada

Los seis archivos se copiaron sin modificar a `data/ml/external_inputs/` (excluida de Git y de la
imagen Docker). Sus SHA-256 están en `DATASET_AUDIT.md` y en el manifiesto de cada modelo. Los ZIP se
leen **en memoria** con límites de miembros, tamaño y razón de compresión, y se rechazan rutas
absolutas, `..` y enlaces simbólicos. El notebook de los autores se revisó de forma estática y
**no se ejecutó**: solo usa `os`, `csv` y `pandas`, y escribe CSV locales.

## 3. Dataset A — Universidad de Hohenheim

| Campo | Valor |
|---|---|
| DOI | 10.5281/zenodo.15130001 (artículo relacionado: 10.1109/ACSOS-C66519.2025.00033) |
| Licencia | CC BY 4.0 |
| Sensores | 9 × DHT22 (±0,5 °C), muestreo 5 s, cámara frigorífica |
| Periodo | 2024-09-02 09:45 → 2024-09-11 11:22 (protocolo `Begin`/`End`) |
| Zona horaria | No declarada. [Decisión] Se asume CEST (UTC+2), sin cambio de horario en el periodo |

[Resultado] Datos crudos limpios: sin timestamps inválidos, duplicados ni no monótonos; 4 cabeceras
repetidas por reinicios del registrador; un hueco de 80 min (SENSOR01) y otro de 4,2 días
(SENSOR03, que empieza el 06-09). `Preprocessed.zip` no se usa: deriva de `Raw.zip` y rellena con
NaN para alinear sensores.

**Exclusiones (cada una con su regla):**

| Sensor | Motivo |
|---|---|
| SENSOR01, SENSOR02 | Declarados móviles por los autores. Alcanzan 34–42 °C (fuera de la cámara) y el `experiment_actions.csv` recibido **no** registra sus movimientos (solo puerta y productos). |
| SENSOR08 | Nunca entra en 2–8 °C (mínimo 9,0 °C; desfase sistemático de unos +3,5 °C frente a sus vecinos). No aporta instantes preventivos, y su único episodio permanente fundiría todos los incidentes de la cámara. |

[Resultado] La cámara **no opera en régimen 2–8 °C de forma estable**: del 2 al 4 de septiembre
permanece por encima de 8 °C (puesta en régimen) y cada jornada de aperturas del protocolo la lleva
a 9–13 °C. Nunca baja de 2 °C: **no hay excursiones bajas**.

[Resultado] Con 6 sensores incluidos hay 51 episodios por sensor, que forman **10 incidentes
independientes** a nivel de cámara (episodios simultáneos de un mismo recinto = un incidente): 5
jornadas de aperturas (05, 06, 09, 10 y 11-09), 4 picos nocturnos breves (≤ 8,3 °C, compatibles con
desescarche: [Hipótesis] no verificable con los datos) y el transitorio de puesta en régimen. **Las
filas no son eventos**: ~90 000 puntos-minuto se reducen a 10 eventos independientes.

[Limitación] Transferencia al dominio farmacéutico débil: cámara grande (gran inercia térmica, ruido
de ±0,07 °C/min) frente a refrigeradores pequeños; aperturas de 5–30 min frente a segundos en una
botica; DHT22 frente a DS18B20.

## 4. Dataset B — Hospital (cadena de frío)

| Campo | Valor |
|---|---|
| DOI | 10.5281/zenodo.22101912 · Licencia CC BY 4.0 |
| Equipos | MHT2025-001, BLD26-001, ULT26-001 · ~1 lectura/min · 2026-08-21 → 2026-08-25 (≈ 93 h) |
| Zona horaria | ISO 8601 con `Z` (UTC). Para agrupar por día se asume Lima (UTC−5) |

[Resultado] **Cabecera incompleta**: 9 nombres, pero 13 279 de 16 379 filas traen 12 campos. Los 3
campos extra (sin nombre) se conservan como `ref_dispositivo_sin_etiqueta`,
`temp_aux_sin_etiqueta` (~20–25 °C, probablemente ambiental) y `updated_at_sin_etiqueta`. Se
verificó que **no desplazan** `dsTemperature`: sus medianas coinciden entre filas de 9 y de 12
campos. Su semántica no está documentada, así que no se usan como variables. 68 lecturas sin
`dsTemperature` quedan como NaN.

**Exclusión:** ULT26-001 (mediana −31,5 °C) es un ultracongelador; **no se transforma** para
parecerse a un refrigerador.

[Resultado] MHT2025-001 se mantiene entre 3,1 y 5,4 °C: **0 excursiones**. BLD26-001 tiene **5
episodios**, todos el 24-08 (00:55–12:25 UTC), breves (1–6 min, pico 9,8 °C) y coincidentes con
aperturas de puerta registradas.

## 5. Decisión sobre las fuentes

| Criterio | Hohenheim | Hospital |
|---|---|---|
| Lecturas en 2–8 °C | Sí (desde el 04-09) | Sí |
| Excursiones reales | Sí, solo altas | 5, un equipo, un día |
| Secuencia reconstruible | Sí | Sí |
| Eventos independientes | **10** | **5** |
| Muestreo apto para H = 30 min | Sí (5 s) | Sí (60 s) |
| Transferencia a ColdCare | Débil (cámara grande, DHT22) | Media (DS18B20, refrigerador), pero datos insuficientes |

[Decisión] Hohenheim es la **única fuente con la que se puede entrenar**, como banco de pruebas
metodológico y no como validación farmacéutica. El hospital solo sirve como prueba externa
(Experimento C). Las fuentes **no se mezclan**: unirlas añadiría filas, no eventos independientes,
y mezclaría dinámicas incompatibles.
