# S7 — repetición 5

- Ventana: 2026-11-06T03:01:15Z → 2026-11-06T03:33:50Z (32.6 min)
- Lecturas guardadas: **196**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 188; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Corte visto en los datos: última en línea 2026-11-06T03:02:08.000000Z, reconexión 2026-11-06T03:33:10.542832Z (1862.5 s); vaciado 38.7 s (4.86 registros/s); sin_datos a los 29.8 s de la última lectura en línea
- Mensajes de lectura en el broker: 199; reenvíos (misma boot_id+seq_no): 3; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 898.748, p95 1757.026, máx 1852.543 (n=196); solo en línea (≤20 s): mediana -0.062, p95 8.946 (n=8)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 963 | 986–1181 | 196 | 196 | 0 | synced 196 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T03:02:16Z | broker detenido | 21.8 | None |
| 2026-11-06T03:32:30Z | broker arriba | None | None |

Incidentes: sin_datos 2026-11-06T03:02:37.848767Z
