# S6 — repetición 2

- Ventana: 2026-11-06T00:27:16Z → 2026-11-06T00:28:44Z (1.5 min)
- Lecturas guardadas: **9**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 1; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Corte visto en los datos: última en línea 2026-11-06T00:27:38.000000Z, reconexión 2026-11-06T00:28:13.574408Z (35.6 s); vaciado 0.0 s (1000.0 registros/s); sin_datos a los 30.3 s de la última lectura en línea
- Mensajes de lectura en el broker: 11; reenvíos (misma boot_id+seq_no): 2; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 0.386, p95 21.649, máx 25.574 (n=9); solo en línea (≤20 s): mediana 0.383, p95 12.319 (n=8)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 963 | 62–70 | 9 | 9 | 0 | synced 9 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T00:27:41Z | falla armada | 27.3 | None |
| 2026-11-06T00:27:49Z | backend caido | 19.3 | None |
| 2026-11-06T00:28:14Z | backend arriba | None | None |

Incidentes: sin_datos 2026-11-06T00:28:08.291511Z

Falla del COMMIT (2026-11-06T00:27:41Z → 2026-11-06T00:27:49Z): 1 lecturas expuestas (capturadas o llegadas al broker durante la falla); 1 quedaron guardadas al final (pérdida del diseño: 0). Pérdida estimada de un diseño que borra con el PUBACK: 1. Llegadas al broker de esas lecturas: 3 (reenvíos: 2).
