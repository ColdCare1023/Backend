# S2 — repetición 4

- Ventana: 2026-11-05T19:37:41Z → 2026-11-05T19:54:11Z (16.5 min)
- Lecturas guardadas: **99**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 70; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Corte visto en los datos: última en línea 2026-11-05T19:42:52.000000Z, reconexión 2026-11-05T19:53:33.252883Z (641.3 s); vaciado 15.8 s (3.98 registros/s); sin_datos a los 30.8 s de la última lectura en línea
- Cortes en la corrida: 2 (el principal es el de más diferidas; los demás no se provocaron): 19:38:32 95.4 s, 7 diferidas; 19:42:52 641.3 s, 63 diferidas
- Mensajes de lectura en el broker: 99; reenvíos (misma boot_id+seq_no): 0; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 152.683, p95 583.5, máx 631.253 (n=99); solo en línea (≤20 s): mediana 0.895, p95 13.302 (n=29)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 897 | 292–390 | 99 | 99 | 0 | synced 99 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-05T19:42:58Z | hotspot apagado | 24.8 | None |
| 2026-11-05T19:53:47Z | hotspot encendido | None | None |

Incidentes: sin_datos 2026-11-05T19:39:02.803896Z; sin_datos 2026-11-05T19:43:22.800383Z
