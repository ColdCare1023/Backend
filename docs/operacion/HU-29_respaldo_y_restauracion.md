# HU-29 — Respaldo programado y prueba de restauración controlada

## Qué se respalda y cómo se verifica

| Paso | Script | Evidencia |
|---|---|---|
| Respaldo diario | `python -m scripts.respaldo_postgres --destino <dir>` | `coldcare_<UTC>.dump`, `coldcare_<UTC>.manifiesto.json`, `RESPALDO_COMPLETADO` en `audit_logs` |
| Prueba de restauración (al menos una vez por sprint y antes de cada entrega) | `python -m scripts.verificar_restauracion --manifiesto <manifiesto>` | `coldcare_<UTC>.restauracion.json` con filas y SHA-256 por tabla |
| Fallo del respaldo (Escenario 3) | — | log `CRITICAL RESPALDO FALLIDO`, `RESPALDO_FALLIDO` en `audit_logs`, código de salida 2 |

El manifiesto guarda, para `thermal_readings`, `traceability_records` y
`audit_logs`, el número de filas y el SHA-256 de sus columnas de evidencia en
orden de clave primaria (`src/infrastructure/respaldo/huellas.py`). La prueba de
restauración recalcula lo mismo en la base restaurada: si una sola fila
difiere, la huella difiere.

## Railway

1. Servicio adicional **cron** con la misma imagen del backend (el Dockerfile ya
   trae `postgresql-client`), comando `python -m scripts.respaldo_postgres
   --destino /respaldos`, programación `0 7 * * *` (02:00 hora de Perú) y un
   volumen montado en `/respaldos`. Variables: `DATABASE_URL` (referencia al
   PostgreSQL del proyecto) y `ENVIRONMENT=production`.
2. Notificación de despliegue fallido activada en el proyecto: un respaldo que
   sale con código 2 aparece como ejecución fallida y llega al correo del
   equipo.
3. Base aislada para la prueba: un segundo servicio PostgreSQL (o una base
   local) cuyo URL se pasa en `RESTAURACION_DATABASE_URL`. El script se niega
   a restaurar sobre `DATABASE_URL`.

## Criterio de aceptación verificable

- `coincide: true` en el informe `.restauracion.json`.
- `archivo_integro: true` (el respaldo no cambió desde que se generó).
- Captura del log del cron con un `RESPALDO_FALLIDO` provocado (por ejemplo,
  `DATABASE_URL` con una contraseña errónea) para el Escenario 3.

## Pendiente de ejecutar en el entorno desplegado

Las pruebas automáticas (`tests/integration/test_hu29_respaldo_restauracion.py`)
cubren la huella, la comparación y la alerta crítica. El `pg_dump` y el
`pg_restore` reales solo se ejercen contra el PostgreSQL de Railway.
