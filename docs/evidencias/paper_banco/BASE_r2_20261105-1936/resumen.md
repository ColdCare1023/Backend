# BASE — repetición 2

- Ventana: 2026-11-06T00:36:43Z → 2026-11-06T00:39:57Z (3.2 min)
- Lecturas guardadas: **19**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 13; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Corte visto en los datos: última en línea 2026-11-06T00:37:08.000000Z, reconexión 2026-11-06T00:39:36.557586Z (148.6 s); vaciado 2.3 s (5.54 registros/s); sin_datos a los 30.2 s de la última lectura en línea
- Mensajes de lectura en el broker: 24; reenvíos (misma boot_id+seq_no): 5; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 50.336, p95 129.749, máx 138.558 (n=19); solo en línea (≤20 s): mediana 0.356, p95 8.64 (n=6)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 963 | 119–137 | 19 | 19 | 0 | synced 19 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T00:37:18Z | falla on | 20.2 | None |
| 2026-11-06T00:39:18Z | falla off | None | None |

Incidentes: sin_datos 2026-11-06T00:37:38.244841Z

Falla del COMMIT (2026-11-06T00:37:18Z → 2026-11-06T00:39:18Z): 13 lecturas expuestas (capturadas o llegadas al broker durante la falla); 13 quedaron guardadas al final (pérdida del diseño: 0). Pérdida estimada de un diseño que borra con el PUBACK: 13. Llegadas al broker de esas lecturas: 18 (reenvíos: 5).
