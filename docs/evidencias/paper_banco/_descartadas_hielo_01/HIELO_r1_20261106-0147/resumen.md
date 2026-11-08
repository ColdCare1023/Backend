# HIELO — repetición 1

- Ventana: 2026-11-06T06:47:00Z → 2026-11-06T06:57:00Z (10.0 min)
- Lecturas guardadas: **60**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 0; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Mensajes de lectura en el broker: 60; reenvíos (misma boot_id+seq_no): 0; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 0.855, p95 0.921, máx 0.951 (n=60); solo en línea (≤20 s): mediana 0.855, p95 0.921 (n=60)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 1046 | 10–69 | 60 | 60 | 0 | synced 60 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T06:48:00Z | sonda en hielo | None | None |
| 2026-11-06T06:53:00Z | sonda al refrigerador | None | None |
