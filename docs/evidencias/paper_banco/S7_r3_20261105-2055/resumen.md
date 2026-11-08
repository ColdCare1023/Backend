# S7 — repetición 3

- Ventana: 2026-11-06T01:55:44Z → 2026-11-06T02:28:24Z (32.7 min)
- Lecturas guardadas: **196**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 188; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Corte visto en los datos: última en línea 2026-11-06T01:56:38.000000Z, reconexión 2026-11-06T02:27:40.475792Z (1862.5 s); vaciado 40.3 s (4.66 registros/s); sin_datos a los 30.1 s de la última lectura en línea
- Mensajes de lectura en el broker: 199; reenvíos (misma boot_id+seq_no): 3; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 899.368, p95 1756.987, máx 1852.476 (n=196); solo en línea (≤20 s): mediana 0.123, p95 9.596 (n=8)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 963 | 593–788 | 196 | 196 | 0 | synced 196 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T01:56:46Z | broker detenido | 22.1 | None |
| 2026-11-06T02:27:00Z | broker arriba | None | None |

Incidentes: sin_datos 2026-11-06T01:57:08.054764Z
