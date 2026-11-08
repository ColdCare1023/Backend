# S5 — repetición 2

- Ventana: 2026-11-05T20:20:50Z → 2026-11-05T20:34:02Z (13.2 min)
- Lecturas guardadas: **83**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 59; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Corte visto en los datos: última en línea 2026-11-05T20:24:25.000000Z, reconexión 2026-11-05T20:33:47.287289Z (562.3 s); vaciado 12.5 s (4.71 registros/s); sin_datos a los 31.0 s de la última lectura en línea
- Mensajes de lectura en el broker: 83; reenvíos (misma boot_id+seq_no): 0; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 166.697, p95 515.553, máx 552.287 (n=76); solo en línea (≤20 s): mediana 1.046, p95 3.819 (n=24)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 898 | 27–78 | 52 | 52 | 0 | synced 52 |
| 899 | 1–4 | 4 | 4 | 0 | unsynced 1, synced 3 |
| 900 | 1–4 | 4 | 4 | 0 | unsynced 1, synced 3 |
| 901 | 1–4 | 4 | 4 | 0 | unsynced 1, synced 3 |
| 902 | 1–4 | 4 | 4 | 0 | unsynced 1, synced 3 |
| 903 | 1–4 | 4 | 4 | 0 | unsynced 1, synced 3 |
| 904 | 1–4 | 4 | 4 | 0 | unsynced 1, synced 3 |
| 905 | 1–4 | 4 | 4 | 0 | unsynced 1, synced 3 |
| 906 | 1–3 | 3 | 3 | 0 | backfilled 1, synced 2 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-05T20:24:33Z | hotspot apagado | 23.0 | None |
| 2026-11-05T20:29:42Z | reset | None | None |
| 2026-11-05T20:33:52Z | hotspot encendido | None | None |

Incidentes: sin_datos 2026-11-05T20:24:56.048480Z
