# S12b — repetición 3

- Ventana: 2026-11-06T04:55:00Z → 2026-11-06T05:01:00Z (6.0 min)
- Lecturas guardadas: **18**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 0; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Mensajes de lectura en el broker: 18; reenvíos (misma boot_id+seq_no): 0; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 0.035, p95 1.767, máx 5.461 (n=18); solo en línea (≤20 s): mediana 0.035, p95 1.767 (n=18)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 966 | 19–24 | 6 | 6 | 0 | synced 6 |
| 967 | 1–12 | 12 | 12 | 0 | backfilled 1, synced 11 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T04:56:00Z | nodo desenchufado | 22.0 | None |
| 2026-11-06T04:59:00Z | nodo enchufado | None | None |

Incidentes: sin_datos 2026-11-06T04:56:21.994761Z
