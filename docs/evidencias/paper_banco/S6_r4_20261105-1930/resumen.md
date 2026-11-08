# S6 — repetición 4

- Ventana: 2026-11-06T00:30:16Z → 2026-11-06T00:31:45Z (1.5 min)
- Lecturas guardadas: **9**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 1; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Corte visto en los datos: última en línea 2026-11-06T00:30:38.000000Z, reconexión 2026-11-06T00:31:13.796268Z (35.8 s); vaciado 0.0 s (1000.0 registros/s); sin_datos a los 30.3 s de la última lectura en línea
- Mensajes de lectura en el broker: 11; reenvíos (misma boot_id+seq_no): 2; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 0.368, p95 21.892, máx 25.796 (n=9); solo en línea (≤20 s): mediana 0.365, p95 12.603 (n=8)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 963 | 80–88 | 9 | 9 | 0 | synced 9 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T00:30:42Z | falla armada | 26.3 | None |
| 2026-11-06T00:30:49Z | backend caido | 19.3 | None |
| 2026-11-06T00:31:14Z | backend arriba | None | None |

Incidentes: sin_datos 2026-11-06T00:31:08.286988Z

Falla del COMMIT (2026-11-06T00:30:42Z → 2026-11-06T00:30:49Z): 1 lecturas expuestas (capturadas o llegadas al broker durante la falla); 1 quedaron guardadas al final (pérdida del diseño: 0). Pérdida estimada de un diseño que borra con el PUBACK: 1. Llegadas al broker de esas lecturas: 3 (reenvíos: 2).
