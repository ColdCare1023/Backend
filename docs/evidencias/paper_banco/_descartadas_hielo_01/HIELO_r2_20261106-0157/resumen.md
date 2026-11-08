# HIELO — repetición 2

- Ventana: 2026-11-06T06:57:01Z → 2026-11-06T07:07:00Z (10.0 min)
- Lecturas guardadas: **60**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 0; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Mensajes de lectura en el broker: 60; reenvíos (misma boot_id+seq_no): 0; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 0.815, p95 0.885, máx 1.196 (n=59); solo en línea (≤20 s): mediana 0.815, p95 0.885 (n=59)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 1046 | 70–109 | 40 | 40 | 0 | synced 40 |
| 1047 | 1–20 | 20 | 20 | 0 | unsynced 1, synced 19 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T06:58:00Z | sonda en hielo | None | None |
| 2026-11-06T07:00:00Z | sonda en hielo | None | None |
| 2026-11-06T07:03:00Z | sonda al refrigerador | None | None |
| 2026-11-06T07:06:00Z | sonda al refrigerador | None | None |
