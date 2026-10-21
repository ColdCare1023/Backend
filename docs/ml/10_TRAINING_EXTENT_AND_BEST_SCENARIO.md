# 10 — Cuánto se entrenó la IA y cuál es el mejor escenario de entrenamiento

## 1. Volumen real de entrenamiento

| Concepto | Cantidad |
|---|---|
| Lecturas crudas leídas (Hohenheim, 9 sensores, cada 5 s) | 1 083 195 |
| Sensores utilizados | 6 de 9 (01, 02 y 08 excluidos) |
| Puntos de rejilla de 1 min de los sensores usados | 50 872 |
| Instantes descartados por estar ya fuera de 2–8 °C (excursión actual) | 20 052 |
| Instantes descartados dentro de un episodio o sin horizonte completo | 324 |
| **Observaciones de desarrollo (entrenamiento + validación)** | **17 116**, de ellas **384 positivas** (2,2 %) |
| **Observaciones de prueba independiente** | **13 380**, de ellas **790 positivas** (5,9 %) |
| Incidentes independientes en desarrollo / prueba | **5 / 5** |
| Periodo de desarrollo / prueba | 02–08-09-2024 / 09–11-09-2024 |
| Combinaciones de hiperparámetros probadas | 24 por experimento × 3 pliegues = 72 ajustes |
| Árboles por modelo | 300 |
| Experimentos ejecutados | A1, A2, A3, C, C2 (B no viable; D pendiente) |
| Modelos registrados | 2 (`…-termico-1.0.0`, 875 KB; `…-termico-puerta-1.0.0`, 145 KB) |
| Tiempo total del pipeline | ≈ 2–3 min en una PC de 24 núcleos |

**Lo importante:** el modelo vio unos 17 000 ejemplos, pero solo **5 eventos independientes** en el
desarrollo. Para un modelo preventivo, la cantidad de eventos independientes es lo que limita, no
la cantidad de filas: cada excursión aporta hasta 30 instantes positivos casi idénticos por sensor, y
varios sensores registran la misma excursión. En términos de aprendizaje, el modelo aprendió de 5
excursiones reales. **Es un entrenamiento mínimo**, suficiente para validar el pipeline, no para
aprobar el modelo.

## 2. Por qué no alcanza

| Limitación | Efecto observado |
|---|---|
| 5 eventos en desarrollo | El primer pliegue de validación entrenó con solo 39 positivos; los hiperparámetros elegidos son inestables entre experimentos |
| Excursiones causadas por aperturas programadas | Con temperatura sola, el RF no supera a la regla de tendencia (AP 0,18 frente a 0,23) |
| Cámara grande, distinta a un refrigerador | La transferencia al hospital cae a nivel de azar (AP 0,025) |
| Sin excursiones bajas | No hay evidencia ante congelación |
| Desfase entre sensores DHT22 | Peor desempeño en el sensor nunca visto (SENSOR05) |

## 3. Mejor escenario para un mejor entrenamiento

Ordenado por impacto esperado:

1. **Datos del propio ColdCare (ESP32 + DS18B20 + MC-38).** Es el factor decisivo. El experimento
   C mostró que la dinámica de una cámara no transfiere a un refrigerador. Activar
   `IA_PREVENTIVO_MODO=sombra` desde el primer día de instalación: cada lectura y su predicción quedan
   guardadas sin afectar alertas.
2. **Instalar MC-38 en todos los refrigeradores.** Fue la única señal que mejoró al RF de forma
   clara (AP 0,51 frente a 0,18 sin puerta; mediana de anticipación 16,5 min).
3. **Cantidad mínima recomendada:**

   | Objetivo | Requisito |
   |---|---|
   | Evaluación concluyente | ≥ 10 incidentes independientes **en la partición de prueba** |
   | Entrenamiento razonable | ≥ 30 incidentes independientes en desarrollo |
   | Diversidad | ≥ 3 refrigeradores (de ser posible, de modelos distintos) y ≥ 2 boticas |
   | Duración | ≥ 4–6 semanas de operación continua (incluye fines de semana y noches) |
   | Muestreo | 30 s (de fábrica); nunca más de 60 s, porque la ventana de 15 min necesita cobertura |

   Con 10 eventos todos anticipados, el límite inferior del IC95 de la sensibilidad es 0,69; con 30,
   llega a 0,88.
4. **Excursiones controladas y documentadas** para llegar antes a esa cantidad: aperturas
   prolongadas (1, 3, 5 y 10 min), carga de producto tibio, desconexión eléctrica breve y, si el
   protocolo de seguridad lo permite, enfriamiento excesivo para tener **excursiones bajas**. Cada
   una debe registrarse con hora de inicio, tipo y responsable (equivalente a `experiment_actions.csv`).
   Usar refrigeradores **sin medicamentos reales** durante las pruebas.
5. **Calibrar los sensores** contra un termómetro de referencia antes de recolectar, para que el
   modelo no dependa del desfase de cada sensor.
6. **Partición por tiempo y por refrigerador:** entrenar con unos equipos o semanas y probar con
   otros nunca vistos. Nunca dividir por filas.
7. **Recién con ≥ 10 incidentes de validación:** calibrar la puntuación (sigmoide sobre una
   partición separada), para poder mostrarla como probabilidad.
8. **Reentrenar periódicamente** (por ejemplo, mensual) como una **versión nueva** del registro, y
   aprobarla solo si supera los criterios de `CRITERIOS_ACEPTACION` frente a la versión vigente.

## 4. Flujo recomendado para TP2

```
Semana 0     Instalar ESP32 + DS18B20 + MC-38; calibrar; modo sombra con rf-preventivo-h30-termico-puerta-1.0.0
Semanas 1-4  Operación normal + excursiones controladas documentadas
Semana 5     Exportar thermal_readings → evaluar-coldcare (transferencia del modelo actual)
Semana 5     Reentrenar con datos ColdCare (mismo pipeline, ingesta.importar_coldcare_csv)
Semana 6     Evaluar con criterios; si cumple → VALIDATED_OFFLINE → aprobación firmada → modo activo
```

Hasta cumplir esos criterios, el sistema sigue protegido por la regla determinista 2–8 °C, que
genera la alerta crítica sin depender de la IA.
