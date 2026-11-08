# BASE — repetición 5

- Ventana: 2026-11-06T00:46:38Z → 2026-11-06T00:49:53Z (3.2 min)
- Lecturas guardadas: **20**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 13; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Corte visto en los datos: última en línea 2026-11-06T00:46:58.000000Z, reconexión 2026-11-06T00:49:26.664577Z (148.7 s); vaciado 2.4 s (5.35 registros/s); sin_datos a los 30.3 s de la última lectura en línea
- Mensajes de lectura en el broker: 24; reenvíos (misma boot_id+seq_no): 5; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 45.578, p95 129.387, máx 138.665 (n=20); solo en línea (≤20 s): mediana 0.34, p95 8.354 (n=7)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 963 | 178–197 | 20 | 20 | 0 | synced 20 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T00:47:13Z | falla on | 15.3 | None |
| 2026-11-06T00:49:14Z | falla off | None | None |

Incidentes: sin_datos 2026-11-06T00:47:28.277220Z

Falla del COMMIT (2026-11-06T00:47:13Z → 2026-11-06T00:49:14Z): 12 lecturas expuestas (capturadas o llegadas al broker durante la falla); 12 quedaron guardadas al final (pérdida del diseño: 0). Pérdida estimada de un diseño que borra con el PUBACK: 12. Llegadas al broker de esas lecturas: 12 (reenvíos: 0).
