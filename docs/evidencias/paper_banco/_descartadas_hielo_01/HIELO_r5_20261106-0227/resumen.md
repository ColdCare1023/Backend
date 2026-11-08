# HIELO — repetición 5

- Ventana: 2026-11-06T07:27:01Z → 2026-11-06T07:35:00Z (8.0 min)
- Lecturas guardadas: **49**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 0; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 1
- Mensajes de lectura en el broker: 49; reenvíos (misma boot_id+seq_no): 0; vistas en el broker sin guardar: 1
- Latencia captura→COMMIT (s): mediana -0.481, p95 0.69, máx 5.009 (n=48); solo en línea (≤20 s): mediana -0.481, p95 0.69 (n=48)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 1049 | 22–29 | 8 | 8 | 0 | synced 8 |
| 1051 | 1–36 | 36 | 36 | 0 | backfilled 1, synced 35 |
| 1052 | 1–5 | 5 | 5 | 0 | unsynced 1, synced 4 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T07:28:00Z | sonda en hielo | None | None |
| 2026-11-06T07:30:00Z | sonda al refrigerador | None | None |
| 2026-11-06T07:33:00Z | sonda al refrigerador | None | None |
