# S7 — repetición 4

- Ventana: 2026-11-06T02:28:27Z → 2026-11-06T03:01:12Z (32.8 min)
- Lecturas guardadas: **197**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 187; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Corte visto en los datos: última en línea 2026-11-06T02:29:28.000000Z, reconexión 2026-11-06T03:00:22.294525Z (1854.3 s); vaciado 41.0 s (4.56 registros/s); sin_datos a los 30.0 s de la última lectura en línea
- Mensajes de lectura en el broker: 199; reenvíos (misma boot_id+seq_no): 3; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 886.775, p95 1748.444, máx 1844.295 (n=197); solo en línea (≤20 s): mediana 0.039, p95 11.042 (n=10)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 963 | 789–985 | 197 | 197 | 0 | synced 197 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T02:29:28Z | broker detenido | 30.0 | None |
| 2026-11-06T02:59:43Z | broker arriba | None | None |

Incidentes: sin_datos 2026-11-06T02:29:57.976012Z
