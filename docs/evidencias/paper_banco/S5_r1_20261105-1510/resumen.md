# S5 — repetición 1

- Ventana: 2026-11-05T20:10:51Z → 2026-11-05T20:20:31Z (9.7 min)
- Lecturas guardadas: **59**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 41; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Corte visto en los datos: última en línea 2026-11-05T20:13:12.000000Z, reconexión 2026-11-05T20:20:15.017543Z (423.0 s); vaciado 7.9 s (5.22 registros/s); sin_datos a los 30.7 s de la última lectura en línea
- Mensajes de lectura en el broker: 59; reenvíos (misma boot_id+seq_no): 0; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 135.765, p95 384.575, máx 413.018 (n=59); solo en línea (≤20 s): mediana 0.818, p95 9.708 (n=18)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 897 | 491–525 | 35 | 35 | 0 | synced 35 |
| 898 | 1–24 | 24 | 24 | 0 | backfilled 23, synced 1 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-05T20:13:22Z | hotspot apagado | 20.7 | None |
| 2026-11-05T20:16:24Z | reset | None | None |
| 2026-11-05T20:20:04Z | hotspot encendido | None | None |

Incidentes: sin_datos 2026-11-05T20:13:42.715542Z
