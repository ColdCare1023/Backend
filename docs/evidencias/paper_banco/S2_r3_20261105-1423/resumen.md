# S2 — repetición 3

- Ventana: 2026-11-05T19:23:09Z → 2026-11-05T19:37:35Z (14.4 min)
- Lecturas guardadas: **87**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 64; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Corte visto en los datos: última en línea 2026-11-05T19:26:12.000000Z, reconexión 2026-11-05T19:36:59.896580Z (647.9 s); vaciado 14.5 s (4.41 registros/s); sin_datos a los 30.9 s de la última lectura en línea
- Mensajes de lectura en el broker: 87; reenvíos (misma boot_id+seq_no): 0; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 218.465, p95 595.844, máx 637.897 (n=87); solo en línea (≤20 s): mediana 0.966, p95 11.273 (n=23)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 897 | 205–291 | 87 | 87 | 0 | synced 87 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-05T19:26:16Z | hotspot apagado | 26.9 | None |
| 2026-11-05T19:36:57Z | hotspot encendido | None | None |

Incidentes: sin_datos 2026-11-05T19:26:42.861857Z
