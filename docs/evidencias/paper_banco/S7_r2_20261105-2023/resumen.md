# S7 — repetición 2

- Ventana: 2026-11-06T01:23:02Z → 2026-11-06T01:55:42Z (32.7 min)
- Lecturas guardadas: **196**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 187; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Corte visto en los datos: última en línea 2026-11-06T01:23:58.000000Z, reconexión 2026-11-06T01:54:57.186523Z (1859.2 s); vaciado 39.5 s (4.73 registros/s); sin_datos a los 30.1 s de la última lectura en línea
- Mensajes de lectura en el broker: 199; reenvíos (misma boot_id+seq_no): 3; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 894.776, p95 1753.983, máx 1849.187 (n=196); solo en línea (≤20 s): mediana 0.218, p95 14.98 (n=9)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 963 | 397–592 | 196 | 196 | 0 | synced 196 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T01:24:03Z | broker detenido | 25.1 | None |
| 2026-11-06T01:54:18Z | broker arriba | None | None |

Incidentes: sin_datos 2026-11-06T01:24:28.114606Z
