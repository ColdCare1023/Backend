# S2 — repetición 5

- Ventana: 2026-11-05T19:54:47Z → 2026-11-05T20:10:27Z (15.7 min)
- Lecturas guardadas: **94**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 63; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Corte visto en los datos: última en línea 2026-11-05T19:59:22.000000Z, reconexión 2026-11-05T20:10:02.434857Z (640.4 s); vaciado 12.8 s (4.94 registros/s); sin_datos a los 30.8 s de la última lectura en línea
- Mensajes de lectura en el broker: 28; reenvíos (misma boot_id+seq_no): 0; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 175.016, p95 585.027, máx 630.435 (n=94); solo en línea (≤20 s): mediana 0.854, p95 2.245 (n=31)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 897 | 395–488 | 94 | 94 | 0 | synced 94 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-05T19:59:19Z | hotspot apagado | 33.8 | None |
| 2026-11-05T20:09:48Z | hotspot encendido | None | None |

Incidentes: sin_datos 2026-11-05T19:59:52.847990Z
