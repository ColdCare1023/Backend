# BASE — repetición 3

- Ventana: 2026-11-06T00:40:01Z → 2026-11-06T00:43:15Z (3.2 min)
- Lecturas guardadas: **19**; faltantes (huecos de seq_no): **0**; duplicadas en la base: **0**
- Diferidas (guardadas en el nodo y reenviadas, latencia > 20 s): 13; confirmadas fuera de orden: 0; llegadas sin hora de captura (fuera del análisis, ver sin_hora.csv): 0
- Corte visto en los datos: última en línea 2026-11-06T00:40:18.000000Z, reconexión 2026-11-06T00:42:46.528909Z (148.5 s); vaciado 2.6 s (5.06 registros/s); sin_datos a los 30.3 s de la última lectura en línea
- Mensajes de lectura en el broker: 24; reenvíos (misma boot_id+seq_no): 5; vistas en el broker sin guardar: 0
- Latencia captura→COMMIT (s): mediana 50.412, p95 129.719, máx 138.529 (n=19); solo en línea (≤20 s): mediana 0.331, p95 8.831 (n=6)

| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |
|---|---|---|---|---|---|
| 963 | 139–157 | 19 | 19 | 0 | synced 19 |

| marca (UTC) | evento | s hasta incidente | s hasta alerta |
|---|---|---|---|
| 2026-11-06T00:40:36Z | falla on | 12.3 | None |
| 2026-11-06T00:42:36Z | falla off | None | None |

Incidentes: sin_datos 2026-11-06T00:40:48.257190Z

Falla del COMMIT (2026-11-06T00:40:36Z → 2026-11-06T00:42:36Z): 12 lecturas expuestas (capturadas o llegadas al broker durante la falla); 12 quedaron guardadas al final (pérdida del diseño: 0). Pérdida estimada de un diseño que borra con el PUBACK: 12. Llegadas al broker de esas lecturas: 12 (reenvíos: 0).
