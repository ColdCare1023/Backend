# HIELO — repetición 3

- Ventana: 2026-11-06T07:07:02Z → 2026-11-06T07:11:00Z (4.0 min)
- Lecturas guardadas: **23**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 0; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Mensajes de lectura en el broker: 23; reenvíos (misma boot_id+seq_no): 0; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 0.751, p95 0.775, máx 0.83 (n=23); solo en línea (≤20 s): mediana 0.751, p95 0.775 (n=23)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 1047 | 22–44 | 23 | 23 | 0 | synced 23 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T07:08:00Z | sonda en hielo | None | None |
