# S5 — repetición 5

- Ventana: 2026-11-06T00:00:32Z → 2026-11-06T00:10:22Z (9.8 min)
- Lecturas guardadas: **59**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 41; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Corte visto en los datos: última en línea 2026-11-06T00:02:59.000000Z, reconexión 2026-11-06T00:10:07.743852Z (428.7 s); vaciado 8.1 s (5.06 registros/s); sin_datos a los 30.2 s de la última lectura en línea
- Mensajes de lectura en el broker: 59; reenvíos (misma boot_id+seq_no): 0; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 135.798, p95 390.417, máx 418.744 (n=59); solo en línea (≤20 s): mediana 0.247, p95 9.648 (n=18)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 956 | 44–78 | 35 | 35 | 0 | synced 35 |
| 957 | 1–24 | 24 | 24 | 0 | backfilled 23, synced 1 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T00:03:09Z | hotspot apagado | 20.2 | None |
| 2026-11-06T00:06:20Z | reset | None | None |
| 2026-11-06T00:09:49Z | hotspot encendido | None | None |

Incidentes: sin_datos 2026-11-06T00:03:29.172460Z
