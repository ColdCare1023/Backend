# S9 — repetición 5

- Ventana: 2026-11-06T04:16:00Z → 2026-11-06T04:23:00Z (7.0 min)
- Lecturas guardadas: **42**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 0; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Mensajes de lectura en el broker: 42; reenvíos (misma boot_id+seq_no): 0; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 0.81, p95 0.853, máx 0.902 (n=42); solo en línea (≤20 s): mediana 0.81, p95 0.853 (n=42)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 963 | 1435–1476 | 42 | 42 | 0 | synced 42 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T04:17:00Z | puerta abierta | None | None |
| 2026-11-06T04:22:00Z | puerta cerrada | None | None |
