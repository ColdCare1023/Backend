# S5 — repetición 3

- Ventana: 2026-11-05T23:33:56Z → 2026-11-05T23:45:18Z (11.4 min)
- Lecturas guardadas: **68**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 61; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 457
- Corte visto en los datos: última en línea 2026-11-05T23:37:51.000000Z, reconexión 2026-11-05T23:44:58.613498Z (427.6 s); vaciado 8.7 s (4.7 registros/s); sin_datos a los 31.5 s de la última lectura en línea
- Cortes en la corrida: 2 (el principal es el de más diferidas; los demás no se provocaron):  None s, 20 diferidas; 23:37:51 427.6 s, 41 diferidas
- Mensajes de lectura en el broker: 915; reenvíos (misma boot_id+seq_no): 0; vistas en el broker sin guardar: 847
- Latencia captura→COMMIT (s): mediana 153.335, p95 384.847, máx 417.613 (n=68); solo en línea (≤20 s): mediana 4.658, p95 17.281 (n=7)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 954 | 8–51 | 44 | 44 | 0 | synced 44 |
| 955 | 1–24 | 24 | 24 | 0 | backfilled 22, synced 2 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-05T23:37:58Z | hotspot apagado | 24.5 | None |
| 2026-11-05T23:41:11Z | reset | None | None |
| 2026-11-05T23:44:34Z | hotspot encendido | None | None |

Incidentes: sin_datos 2026-11-05T23:38:22.457007Z
