# BASE — repetición 4

- Ventana: 2026-11-06T00:43:19Z → 2026-11-06T00:46:34Z (3.2 min)
- Lecturas guardadas: **19**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 13; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Corte visto en los datos: última en línea 2026-11-06T00:43:38.000000Z, reconexión 2026-11-06T00:46:06.570414Z (148.6 s); vaciado 2.5 s (5.2 registros/s); sin_datos a los 30.2 s de la última lectura en línea
- Mensajes de lectura en el broker: 24; reenvíos (misma boot_id+seq_no): 5; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 50.534, p95 129.776, máx 138.57 (n=19); solo en línea (≤20 s): mediana 0.305, p95 8.804 (n=6)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 963 | 159–177 | 19 | 19 | 0 | synced 19 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T00:43:55Z | falla on | 13.2 | None |
| 2026-11-06T00:45:55Z | falla off | None | None |

Incidentes: sin_datos 2026-11-06T00:44:08.228917Z

Falla del COMMIT (2026-11-06T00:43:55Z → 2026-11-06T00:45:55Z): 12 lecturas expuestas (capturadas o llegadas al broker durante la falla); 12 quedaron guardadas al final (pérdida del diseño: 0). Pérdida estimada de un diseño que borra con el PUBACK: 12. Llegadas al broker de esas lecturas: 12 (reenvíos: 0).
