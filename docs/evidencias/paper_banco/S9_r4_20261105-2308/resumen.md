# S9 — repetición 4

- Ventana: 2026-11-06T04:08:00Z → 2026-11-06T04:15:00Z (7.0 min)
- Lecturas guardadas: **42**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 0; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Mensajes de lectura en el broker: 42; reenvíos (misma boot_id+seq_no): 0; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 0.803, p95 0.86, máx 1.813 (n=42); solo en línea (≤20 s): mediana 0.803, p95 0.86 (n=42)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 963 | 1387–1428 | 42 | 42 | 0 | synced 42 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T04:09:00Z | puerta abierta | None | None |
| 2026-11-06T04:14:00Z | puerta cerrada | None | None |
