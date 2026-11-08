# S7 — repetición 1

- Ventana: 2026-11-06T00:51:01Z → 2026-11-06T01:22:59Z (32.0 min)
- Lecturas guardadas: **192**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 184; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Corte visto en los datos: última en línea 2026-11-06T00:51:58.000000Z, reconexión 2026-11-06T01:22:19.356636Z (1821.4 s); vaciado 39.1 s (4.7 registros/s); sin_datos a los 30.2 s de la última lectura en línea
- Mensajes de lectura en el broker: 192; reenvíos (misma boot_id+seq_no): 0; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 877.387, p95 1718.0, máx 1811.357 (n=192); solo en línea (≤20 s): mediana 0.312, p95 7.289 (n=8)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 963 | 205–396 | 192 | 192 | 0 | synced 192 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T00:52:03Z | broker detenido | 25.2 | None |
| 2026-11-06T01:22:17Z | broker arriba | None | None |

Incidentes: sin_datos 2026-11-06T00:52:28.202555Z
