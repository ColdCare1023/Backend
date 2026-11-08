# S6 — repetición 5

- Ventana: 2026-11-06T00:31:46Z → 2026-11-06T00:33:15Z (1.5 min)
- Lecturas guardadas: **9**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 1; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Corte visto en los datos: última en línea 2026-11-06T00:32:08.000000Z, reconexión 2026-11-06T00:32:44.122811Z (36.1 s); vaciado 0.0 s (1000.0 registros/s); sin_datos a los 30.2 s de la última lectura en línea
- Mensajes de lectura en el broker: 11; reenvíos (misma boot_id+seq_no): 2; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 0.302, p95 22.207, máx 26.123 (n=9); solo en línea (≤20 s): mediana 0.299, p95 12.908 (n=8)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 963 | 89–97 | 9 | 9 | 0 | synced 9 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T00:32:11Z | falla armada | 27.2 | None |
| 2026-11-06T00:32:19Z | backend caido | 19.2 | None |
| 2026-11-06T00:32:45Z | backend arriba | None | None |

Incidentes: sin_datos 2026-11-06T00:32:38.203871Z

Falla del COMMIT (2026-11-06T00:32:11Z → 2026-11-06T00:32:19Z): 1 lecturas expuestas (capturadas o llegadas al broker durante la falla); 1 quedaron guardadas al final (pérdida del diseño: 0). Pérdida estimada de un diseño que borra con el PUBACK: 1. Llegadas al broker de esas lecturas: 3 (reenvíos: 2).
