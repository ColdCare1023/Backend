# 11 — Reentrenamiento con excursiones reales de banco (17–21 nov. 2026)

Responde al punto 1 de `10_TRAINING_EXTENT_AND_BEST_SCENARIO.md` (§3): conseguir datos del propio
ColdCare (ESP32 + DS18B20 + MC-38) y reentrenar con ellos. Es la evaluación formal con corridas
controladas de HU-17/18 (`python -m src.infrastructure.ai.prediccion_preventiva corridas`).

## 1. Datos

| Concepto | Valor |
|---|---|
| Nodo | Placa de `ESP32_FARMACIA_01` enviando como `ESP32_BANCO_03` (no mezcla datos inducidos con la curva real) |
| Montaje | DS18B20 desnuda, al aire, al centro de un refrigerador de cocina **sin medicamentos**; MC-38 en la puerta; lectura cada 10 s |
| Corridas válidas | **29**: 23 de excursión (puerta 1, 3, 5, 7 y 10 min; 2 botellas tibias 20 min) y 6 de operación normal (S-N) |
| Corridas anuladas | 6 (sonda tocada, puerta no abierta, transitorio al colgar la sonda, S-N arrancada por error); no entran en ninguna cifra |
| Lecturas | 31 653; 0 faltantes, 0 duplicadas (incluye un corte de 6,5 h salvado por el buffer del nodo) |
| Excursiones altas válidas (> 8 °C) | **20**: SP10 ×5, SP5 ×4, SP3 ×4, SP7 ×3, SC ×3, SP1 ×1 |
| Negativos difíciles | 3 acciones sin excursión: SP1 ×2 (7,00 y 7,25 °C) y SC con 1 botella (7,25 °C) |
| Operación normal | 65,7 h, ≈ 48 h limpias tras ajustar el termostato; 9 excursiones bajas y 1 alta leve espontáneas |

Cada corrida: base estable de 20 min (3 min en 2,5–7,5 °C antes de empezar), acción a la hora que
indica el script y 30 min de cola. La hora real de la acción es la del MC-38.

Los datos crudos no están en git (`infra-local/evidencia/` está en `.gitignore`). Están en
`infra-local/evidencia/ml/` en la laptop de banco y en el paquete `ColdCare_IA_banco_2026-11-21_v2.zip`.

## 2. Método

- Exportación: `python infra-local/scripts/ml_exportar.py` → `corridas.csv` (formato de
  `ingesta.importar_coldcare_csv`, con `corrida_id`). 9 lecturas descartadas por `time_quality = unsynced`.
- Pipeline sin cambios: rejilla de 1 min, H = 30 min, RF de 300 árboles con hiperparámetros fijos
  (`max_depth=8`, `min_samples_leaf=10`, `max_features="sqrt"`, `class_weight="balanced_subsample"`),
  umbral elegido sobre entrenamiento.
- Conjuntos de features: `termico_minimo` y `termico_ambiente` (comando `corridas`) y `termico_puerta`
  (misma función `evaluar_conjunto`, llamada aparte porque el comando no la incluye).
- **Partición cronológica por corrida**, nunca por filas:
  - Entrenamiento: 13 corridas del 17 nov. 21:11 al 19 nov. 07:56 (sesiones 1–3), con 9 excursiones altas.
  - Prueba: 16 corridas del 19 nov. 07:56 al 21 nov. 18:00 (sesiones 4–5), con 11 excursiones altas.
  - Separación mínima entre particiones: 44 min (≥ H).
- 3 349 observaciones (801 de entrenamiento y 2 548 de prueba); 13 incidentes evaluables en prueba.

## 3. Resultados (partición de prueba)

| Modelo | AP | F1 macro | Sensibilidad por incidente (IC95) | Falsas alarmas / día-sensor | Anticipación mediana |
|---|---|---|---|---|---|
| RF térmico | 0,066 | 0,48 | 0,15 (0,02–0,45) | 14,1 | 13 min (n = 2) |
| RF térmico + ambiente (SHT31) | 0,094 | 0,48 | 0,31 (0,09–0,61) | 14,1 | 8,5 min (n = 4) |
| RF térmico + puerta (MC-38) | 0,069 | 0,47 | 0,15 (0,02–0,45) | 13,6 | 13,5 min (n = 2) |
| Regla de pendiente (baseline) | 0,080 | 0,44 | 0,69 (0,39–0,91) | 16,4 | 1 min (n = 9) |
| Dummy (prior) | 0,047 | 0,49 | 0 | 0 | — |

**Ninguna variante cumple `CRITERIOS_ACEPTACION`** (F1 macro ≥ 0,85; sensibilidad ≥ 0,80 con IC95
inferior ≥ 0,5; ≤ 2 falsas alarmas por día-sensor). No se registra ninguna versión nueva.

Como referencia, la regla determinista 2–8 °C abrió la alerta crítica con la primera lectura sobre
8 °C en las 19 excursiones evaluables, a una mediana de 80 s de la apertura (28–389 s).

## 4. Interpretación

1. **Una apertura de puerta es exógena.** Nada en la temperatura de los 30 min previos la anuncia, así
   que con H = 30 min ningún modelo la puede anticipar. Lo que la regla de pendiente "anticipa" (1 min)
   es la subida ya iniciada.
2. **La puerta no ayuda con H = 30 min.** Su efecto aparece 40–60 s después de abrir: es una señal de
   detección, no de predicción.
3. **El SHT31 suma poco** (AP +0,03, sensibilidad +0,15). Con 13 incidentes el IC95 es demasiado ancho
   para concluir.
4. **Las falsas alarmas** (≈ 14 por día-sensor) vienen sobre todo de los ciclos del compresor en las S-N,
   que llevan la sonda cerca de 2 °C.

## 5. Consecuencias

- El resultado es negativo y se reporta como tal. La protección de ColdCare es la regla determinista
  2–8 °C; la IA preventiva no reemplaza ni retrasa esa alerta.
- En la farmacia, la IA va en `IA_PREVENTIVO_MODO=sombra`, para acumular eventos reales sin afectar alertas.
- Un RF preventivo necesita eventos con **precursores físicos**: falla gradual del compresor, corte de
  energía, puerta mal cerrada por mucho tiempo o carga tibia grande. Las aperturas puntuales no los tienen.
  Para la próxima recolección conviene inducir ese tipo de eventos, y también evaluar horizontes cortos
  (5–10 min) con la puerta como detección temprana, declarándolos antes de mirar los resultados.
- Siguen vigentes los requisitos de `10_TRAINING_EXTENT_AND_BEST_SCENARIO.md` §3 (≥ 30 incidentes en
  desarrollo, ≥ 10 en prueba, ≥ 3 refrigeradores). Este banco tiene 1 refrigerador, 1 sonda y 1 operadora.

## 6. Reproducir

```bash
cd infra-local
python scripts/ml_exportar.py                        # evidencia/ml/corridas.csv
uv run --no-project --with matplotlib python scripts/ml_derivados.py evidencia/ml <salida>
cd ..
python -m src.infrastructure.ai.prediccion_preventiva corridas \
  --csv infra-local/evidencia/ml/corridas.csv \
  --prueba "$(paste -sd, infra-local/evidencia/ml/rf_reentreno_2026-11-21/particion_prueba.txt)" \
  --salida informe_h30.json
```

Las corridas de banco se generaron con `infra-local/scripts/ml_banco.sh` (orquestador, avisos a la
operadora y marcas), `paper_corrida.py` con `CORRIDAS_DIR=evidencia/ml` y `nodo_identidad.py` para
cambiar la identidad del nodo por consola serie.
