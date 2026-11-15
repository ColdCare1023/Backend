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
`device_config_history` y `ai_model_versions` (registro de versiones del modelo IA). En
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
con sus hashes) se verifica con las columnas que declara.

## Railway

La configuración exacta está en [RESPALDO_RAILWAY.md](RESPALDO_RAILWAY.md): servicio cron con
`Dockerfile.respaldo` y `railway.respaldo.json`, programación `0 7 * * *`, volumen `/respaldos`,
variables, respaldos nativos con su retención y simulacro de restauración. La imagen de la API no
sirve para el cron: su `CMD` levanta uvicorn y en producción exige todos los secretos de la API.

## Criterio de aceptación verificable

- `coincide: true` en el informe `.restauracion.json`.
- `archivo_integro: true` (el respaldo no cambió desde que se generó).
- Captura del log del cron con un `RESPALDO_FALLIDO` provocado (por ejemplo,
  `DATABASE_URL` con una contraseña errónea) para el Escenario 3.

## Pendiente de ejecutar en el entorno desplegado

Las pruebas automáticas cubren la huella, la comparación, la alerta crítica y un respaldo y una
restauración reales con `pg_dump` y `pg_restore` sobre PostgreSQL (pgserver):
`tests/integration/test_hu29_respaldo_restauracion.py` y
`tests/integration/test_hu29_snapshot_postgres.py`. Falta crear el cron en Railway y hacer el
simulacro contra el respaldo de producción (RESPALDO_RAILWAY.md).
