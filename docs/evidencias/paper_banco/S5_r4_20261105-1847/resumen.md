# S5 — repetición 4

- Ventana: 2026-11-05T23:47:34Z → 2026-11-06T00:00:25Z (12.8 min)
- Lecturas guardadas: **77**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 60; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Corte visto en los datos: última en línea 2026-11-05T23:49:59.000000Z, reconexión 2026-11-06T00:00:08.399978Z (609.4 s); vaciado 11.9 s (5.04 registros/s); sin_datos a los 30.6 s de la última lectura en línea
- Mensajes de lectura en el broker: 77; reenvíos (misma boot_id+seq_no): 0; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 227.042, p95 562.373, máx 599.4 (n=77); solo en línea (≤20 s): mediana 0.623, p95 3.709 (n=17)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 955 | 39–73 | 35 | 35 | 0 | synced 35 |
| 956 | 1–42 | 42 | 42 | 0 | backfilled 41, synced 1 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-05T23:50:04Z | hotspot apagado | 25.6 | None |
| 2026-11-05T23:53:24Z | reset | None | None |
| 2026-11-05T23:59:22Z | hotspot encendido | None | None |

Incidentes: sin_datos 2026-11-05T23:50:29.557127Z
