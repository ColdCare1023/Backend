# S2 — repetición 2

- Ventana: 2026-11-05T19:07:30Z → 2026-11-05T19:23:03Z (15.6 min)
- Lecturas guardadas: **94**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 63; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Corte visto en los datos: última en línea 2026-11-05T19:11:42.000000Z, reconexión 2026-11-05T19:22:25.217755Z (643.2 s); vaciado 13.1 s (4.82 registros/s); sin_datos a los 30.9 s de la última lectura en línea
- Mensajes de lectura en el broker: 94; reenvíos (misma boot_id+seq_no): 0; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 178.071, p95 587.582, máx 633.218 (n=94); solo en línea (≤20 s): mediana 0.971, p95 3.959 (n=31)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 897 | 111–204 | 94 | 94 | 0 | synced 94 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-05T19:11:42Z | hotspot apagado | 30.9 | None |
| 2026-11-05T19:22:14Z | hotspot encendido | None | None |
| 2026-11-05T19:22:28Z | hotspot encendido | None | None |

Incidentes: sin_datos 2026-11-05T19:12:12.875353Z
