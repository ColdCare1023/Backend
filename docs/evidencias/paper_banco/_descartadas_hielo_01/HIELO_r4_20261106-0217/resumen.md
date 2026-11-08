# HIELO — repetición 4

- Ventana: 2026-11-06T07:17:01Z → 2026-11-06T07:23:00Z (6.0 min)
- Lecturas guardadas: **36**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 0; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Mensajes de lectura en el broker: 36; reenvíos (misma boot_id+seq_no): 0; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 0.122, p95 0.227, máx 0.256 (n=36); solo en línea (≤20 s): mediana 0.122, p95 0.227 (n=36)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 1048 | 14–49 | 36 | 36 | 0 | synced 36 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T07:18:00Z | sonda en hielo | None | None |
| 2026-11-06T07:18:00Z | sonda al refrigerador | None | None |
| 2026-11-06T07:23:00Z | sonda al refrigerador | None | None |
