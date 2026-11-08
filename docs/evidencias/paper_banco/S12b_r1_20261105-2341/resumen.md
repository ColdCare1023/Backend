# S12b — repetición 1

- Ventana: 2026-11-06T04:41:00Z → 2026-11-06T04:47:00Z (6.0 min)
- Lecturas guardadas: **18**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 0; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Mensajes de lectura en el broker: 18; reenvíos (misma boot_id+seq_no): 0; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 0.744, p95 0.793, máx 0.804 (n=17); solo en línea (≤20 s): mediana 0.744, p95 0.793 (n=17)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 964 | 20–25 | 6 | 6 | 0 | synced 6 |
| 965 | 1–12 | 12 | 12 | 0 | unsynced 1, synced 11 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T04:42:00Z | nodo desenchufado | 23.6 | None |
| 2026-11-06T04:45:00Z | nodo enchufado | None | None |

Incidentes: sin_datos 2026-11-06T04:42:23.593049Z
