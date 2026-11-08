# HIELO — repetición 2

- Ventana: 2026-11-06T07:11:01Z → 2026-11-06T07:17:00Z (6.0 min)
- Lecturas guardadas: **37**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 0; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Mensajes de lectura en el broker: 36; reenvíos (misma boot_id+seq_no): 0; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 0.76, p95 0.845, máx 4.538 (n=36); solo en línea (≤20 s): mediana 0.76, p95 0.845 (n=36)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 1047 | 45–68 | 24 | 24 | 0 | synced 24 |
| 1048 | 1–13 | 13 | 13 | 0 | unsynced 1, synced 12 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T07:12:00Z | sonda en hielo | None | None |
| 2026-11-06T07:13:00Z | sonda al refrigerador | None | None |
