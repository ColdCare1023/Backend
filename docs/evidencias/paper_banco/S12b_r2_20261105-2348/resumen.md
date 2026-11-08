# S12b — repetición 2

- Ventana: 2026-11-06T04:48:00Z → 2026-11-06T04:54:00Z (6.0 min)
- Lecturas guardadas: **18**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 0; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Mensajes de lectura en el broker: 17; reenvíos (misma boot_id+seq_no): 0; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 1.111, p95 1.214, máx 1.225 (n=17); solo en línea (≤20 s): mediana 1.111, p95 1.214 (n=17)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 965 | 19–24 | 6 | 6 | 0 | synced 6 |
| 966 | 1–12 | 12 | 12 | 0 | unsynced 1, synced 11 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T04:49:00Z | nodo desenchufado | 21.6 | None |
| 2026-11-06T04:52:00Z | nodo enchufado | None | None |

Incidentes: sin_datos 2026-11-06T04:49:21.606780Z
