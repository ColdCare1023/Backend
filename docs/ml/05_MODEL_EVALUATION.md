# 05 — Evaluación del modelo

Todas las cifras corresponden a la **prueba independiente** (09–11-09, Hohenheim) salvo que se
indique otra cosa. Prevalencia de `riesgo_preventivo`: 5,9 % (790 de 13 380 instantes). Fuente:
`artefactos/resultados_experimentos.json`.

## 1. A1 — modelo principal (solo temperatura, H = 30 min)

| Modelo | AP | ROC-AUC | Precisión | Recall | F1 riesgo | Bal. acc. | Anticipados ≥ 5 min | Mediana anticipación | Falsas alarmas / sensor-día |
|---|---|---|---|---|---|---|---|---|---|
| **Random Forest** | 0,183 | 0,737 | 0,239 | 0,359 | 0,287 | 0,644 | 65,5 % | 11 min | 4,7 |
| Clase mayoritaria | 0,059 | 0,500 | — | 0,000 | 0,000 | 0,500 | 0 % | — | 0 |
| Proximidad al límite | 0,174 | 0,735 | 0,244 | 0,234 | 0,239 | 0,594 | 37,9 % | 4 min | 3,4 |
| **Tendencia reciente** | **0,228** | 0,731 | 0,246 | 0,305 | 0,272 | 0,623 | 75,9 % | 7 min | 5,6 |
| Regla legada ColdCare | 0,104 | 0,682 | 0,131 | 0,624 | 0,216 | 0,682 | 86,2 % | 16 min | 4,4 |

Accuracy del RF: 0,894, por debajo de la clase mayoritaria (0,941): con 6 % de positivos la
accuracy no sirve para comparar. Matriz de confusión y curvas: `RESULTADOS_EXPERIMENTOS.md`,
`artefactos/fig_pr_prueba.png`.

**Por evento.** Todos los métodos (salvo la clase mayoritaria) emiten al menos una alarma antes de
los 29 episodios evaluables y de los **5/5 incidentes**. IC95 exacto de la sensibilidad por
incidente: [0,48; 1,00]. Contar como "anticipado" un episodio con una sola alarma en los 30 min
previos es una definición laxa, así que se informa también la fracción anticipada con ≥ 5 y ≥ 10
min de margen.

**Lectura honesta:**
- El RF no supera al baseline de tendencia en AP (0,183 frente a 0,228) y genera 4,7 falsas
  alarmas por sensor-día.
- Su ventaja sobre la proximidad pura es mínima (0,183 frente a 0,174; Spearman entre ambas
  puntuaciones: 0,46).
- Lejos del límite (T ≤ 7 °C; prevalencia 3,1 %), el AP es 0,052 (RF) frente a 0,039 (proximidad):
  casi sin capacidad de anticipación cuando la temperatura todavía no se acerca a 8 °C.
- La regla legada alarma mucho (recall 0,62) con muy baja precisión (0,13).

**Incertidumbre.** El IC por bloques (entorno × día) del AP es *no estimable*: la prueba tiene solo
3 bloques independientes. No se presentan intervalos basados en minutos correlacionados.

**Estabilidad por sensor.** AP entre 0,095 (SENSOR05, nunca visto en entrenamiento y con un desfase
de unos +1,5 °C) y 0,359 (SENSOR09). Falsas alarmas entre 1,4 y 8,8 por día, la peor también en
SENSOR05: depender de la temperatura absoluta hace al modelo sensible al desfase de cada sensor.

**Interpretabilidad** (importancia por permutación en prueba, ΔAP): `std_15m` 0,012,
`delta_1min` 0,009, `delta_5m` 0,008, `max_subida_15m` 0,007. Todas pequeñas: ninguna variable
térmica concentra un poder predictivo relevante. La importancia **no implica causalidad**.

**Calibración.** El umbral elegido es 0,749 con `balanced_subsample`: las puntuaciones están
infladas y no son probabilidades (`fig_calibracion.png`; el Brier se informa solo como referencia).

## 2. A2 — variante con señal de puerta

| Modelo | AP | ROC-AUC | Precisión | Recall | F1 | Anticipados ≥ 5 min | Mediana | Falsas / sensor-día |
|---|---|---|---|---|---|---|---|---|
| **Random Forest + puerta** | **0,506** | 0,812 | 0,551 | 0,443 | 0,491 | 86,2 % | 16,5 min | 3,2 |
| Mejor baseline (tendencia) | 0,228 | 0,731 | 0,246 | 0,305 | 0,272 | 75,9 % | 7 min | 5,6 |

[Resultado] Es la única variante en la que el Random Forest aporta un valor claro sobre las reglas:
`puerta_actual` (ΔAP 0,194) y `frac_puerta_15m` (0,075) dominan la importancia. [Limitación] En
Hohenheim, la puerta proviene del protocolo (aperturas de 5–30 min). En una botica las aperturas
duran segundos y el MC-38 es opcional, por lo que no se puede extrapolar sin datos propios.

## 3. A3 — sensibilidad a H = 15 min

RF: AP 0,263 frente a 0,251 (tendencia) y 0,149 (proximidad), pero con el umbral de validación su
recall es solo 0,133 y la mediana de anticipación 2,5 min. Reducir el horizonte no vuelve útil al
modelo térmico. **No se elige el horizonte por estas cifras.**

## 4. C — transferencia al hospital (sin reentrenar)

| Modelo | AP | ROC-AUC | Episodios anticipados | Falsas / sensor-día |
|---|---|---|---|---|
| RF A1 (térmico) | 0,025 | 0,543 | 1/5 | 1,35 |
| RF A2 (+ puerta), C2 | 0,031 | 0,445 | 3/5 | 1,49 |
| Proximidad | 0,032 | 0,512 | 0/5 | 0,27 |
| Regla legada | 0,045 | 0,545 | 1/5 | 1,35 |

Prevalencia: 1,2 %. En MHT2025-001 (estable, 0 excursiones) el RF A1 emite 0 falsas alarmas en ≈
3,7 días; en BLD26-001, 2,7 por día. [Resultado] **La transferencia fracasa**: rendimiento al nivel
del azar. Causa medida: el refrigerador pequeño cicla unas 4 veces más rápido (desviación de
`delta_1min` 0,27 frente a 0,07 °C/min; KS > 0,9 en 17 de 28 features) y opera más frío (4,9 frente
a 6,4 °C). Un modelo de cámara frigorífica no sirve para un refrigerador; ColdCare se parece más
al segundo.

## 5. Decisión (criterios fijados antes de evaluar)

| Criterio | A1 | A2 | Umbral |
|---|---|---|---|
| Incidentes independientes en prueba | 5 ✗ | 5 ✗ | ≥ 10 |
| Sensibilidad por incidente | 1,00 ✓ | 1,00 ✓ | ≥ 0,80 |
| Límite inferior del IC95 | 0,48 ✗ | 0,48 ✗ | ≥ 0,50 |
| Falsas alarmas / sensor-día | 4,7 ✗ | 3,2 ✗ | ≤ 2 |
| Mediana de anticipación | 11 min ✓ | 16,5 min ✓ | ≥ 5 min |
| AP del RF > mejor baseline | ✗ | ✓ | — |

**Estado de ambos modelos: `EXPERIMENTAL`** (eventos independientes insuficientes para una
evaluación concluyente). Ninguno puede activarse en modo `activo`. Aunque hubiera eventos
suficientes, A1 sería rechazado por desempeño.
