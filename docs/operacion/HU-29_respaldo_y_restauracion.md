# HU-29 — Respaldo programado y prueba de restauración controlada

## Qué se respalda y cómo se verifica

| Paso | Script | Evidencia |
|---|---|---|
| Respaldo diario | `python -m scripts.respaldo_postgres --destino <dir>` | `coldcare_<UTC>.dump`, `coldcare_<UTC>.manifiesto.json`, `RESPALDO_COMPLETADO` en `audit_logs` |
| Prueba de restauración (al menos una vez por sprint y antes de cada entrega) | `python -m scripts.verificar_restauracion --manifiesto <manifiesto>` | `coldcare_<UTC>.restauracion.json` con filas y SHA-256 por tabla |
| Fallo del respaldo (Escenario 3) | — | log `CRITICAL RESPALDO FALLIDO`, `RESPALDO_FALLIDO` en `audit_logs`, código de salida 2; en el log del backend, `CRITICAL ALERTA OPERATIVA` (`VigilanteRespaldos`) y `/health/detalle` → `respaldos.estado` |

El manifiesto guarda el número de filas y el SHA-256 de las columnas de evidencia, en orden de
clave primaria (`src/infrastructure/respaldo/huellas.py`), de estas tablas: `thermal_readings`,
`traceability_records`, `audit_logs`, `users`, `devices`, `thermal_alerts`, `corrective_actions`,
`device_config_history`, `roles`, `ai_model_versions` y `predicciones_preventivas`. Se comparan todas
las columnas de estas once tablas, incluidas las métricas y las particiones de aprobación. En
`traceability_records` entra el contenido de cada eslabón (`payload`, `timestamp`, `chain_seq`,
`hash_version`, `usuario_id`, `created_at`), no solo sus hashes, y en `thermal_readings` también la
evidencia de la inferencia (clase, probabilidades, versión del modelo, vector de entrada). La huella se calcula dentro de la misma transacción REPEATABLE READ cuyo
snapshot exportado usa `pg_dump --snapshot`, así que el volcado y la huella describen el mismo
instante aunque el nodo siga publicando. La prueba de restauración recalcula lo mismo en la base
restaurada: si una sola fila difiere, la huella difiere.

En `audit_logs` la huella incluye `resultado` (exitoso, fallido, denegado o bloqueado). El
manifiesto declara en `columnas` las tablas y columnas con las que se calculó. Un manifiesto
anterior, sin `columnas`, se verifica con las tres tablas originales y sin `resultado`, así que
sigue siendo verificable. Del mismo modo, un manifiesto de HU-29 v2 (ocho tablas, eslabones solo
con sus hashes) se verifica con las columnas que declara. El campo `version_huella` identifica
la normalización. La versión 2 conserva toda la precisión de los números de punto flotante.
Los manifiestos anteriores se verifican con la precisión y las columnas que usaron al generarse.

## Railway

La configuración exacta está en [RESPALDO_RAILWAY.md](RESPALDO_RAILWAY.md): servicio cron con
`Dockerfile.respaldo` y `railway.respaldo.json`, programación `0 7 * * *`, volumen `/respaldos`,
variables, respaldos nativos con su retención y simulacro de restauración. La imagen de la API no
sirve para el cron: su `CMD` levanta uvicorn y en producción exige todos los secretos de la API.

## Criterio de aceptación verificable

- `coincide: true` en el informe `.restauracion.json`.
- `archivo_integro: true` (el respaldo no cambió desde que se generó).
- Escenario 3 provocado con la base accesible (`PG_MAJOR` anterior al servidor,
  [RESPALDO_RAILWAY.md § Escenario 3](RESPALDO_RAILWAY.md#escenario-3-fallo-provocado)): captura del
  `CRITICAL RESPALDO FALLIDO` del cron, la fila `RESPALDO_FALLIDO`, el
  `CRITICAL ALERTA OPERATIVA…` en el log del servicio Backend y `/health/detalle` →
  `respaldos.estado = "fallido"`. Una contraseña errónea no sirve: no puede escribir
  `RESPALDO_FALLIDO`.

## Simulacro documentado

El [informe del 15/11/2026](../evidencias/HU-29/2026-11-15_cron_simulacro_y_fallo.md)
registra la restauración aislada de las nueve tablas incluidas en aquel manifiesto y el aviso de
fallo del cron. La próxima prueba operativa debe generar un manifiesto de once tablas y registrar
la comparación de cada tabla.
