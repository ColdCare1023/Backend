# HIELO — repetición 3

- Ventana: 2026-11-06T07:23:02Z → 2026-11-06T07:27:00Z (4.0 min)
- Lecturas guardadas: **24**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 0; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Mensajes de lectura en el broker: 24; reenvíos (misma boot_id+seq_no): 0; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana -0.386, p95 0.127, máx 0.169 (n=23); solo en línea (≤20 s): mediana -0.386, p95 0.127 (n=23)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 1048 | 50–52 | 3 | 3 | 0 | synced 3 |
| 1049 | 1–21 | 21 | 21 | 0 | unsynced 1, synced 20 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T07:24:00Z | sonda en hielo | None | None |
