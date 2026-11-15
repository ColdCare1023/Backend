# HU-29: evidencia en producción (15/11/2026)

Servicio cron `respaldo-postgres` en Railway (proyecto `eloquent-gentleness`, entorno
`production`), desplegado desde la rama `deploy/respaldo-postgres` (merge de `main` 9ccef62,
Backend#36). Horas en Lima (UTC−5) salvo que se diga otra cosa. Las capturas están en el
repositorio de auditoría, en `auditoria/evidencias_operacion/2026-11-15/`.

## Configuración del servicio

| Ajuste | Valor | Dónde queda |
|---|---|---|
| Builder | Dockerfile, `Dockerfile.respaldo` | Panel (*Settings → Build*) y `railway.json` de la rama |
| Programa | `0 7 * * *` (UTC), 02:00 en Lima | Panel (*Settings → Deploy → Cron Schedule*) y `railway.json` de la rama |
| Reinicio | Never | Panel y `railway.json` de la rama |
| Variables | `DATABASE_URL=${{Postgres.DATABASE_URL}}`, `PG_MAJOR=18`, `RESPALDO_DESTINO=/respaldos`, `RESPALDO_RETENER=14` | Panel (*Variables*) |
| Volumen | `respaldo-postgres-volume` montado en `/respaldos` | Panel |
| Vigilancia en la API | `RESPALDO_MAX_ANTIGUEDAD_HORAS=26` en el servicio Backend | Panel (*Variables* del Backend) |

Los tres primeros valores ya están en el panel, así que el cron no depende del `railway.json`
de la rama cuando Railway deje de leer config-as-code (2026-12-01). Captura:
`HU-29_cron_config_en_panel.png`.

## Escenario 1: respaldo programado

- 11:01: `RESPALDO_COMPLETADO` con las 9 tablas (`coldcare_20261115T160051Z`).
- 11:54:04, ya con la imagen de Backend#36 y sin la variable `HOME=/tmp`:
  `Respaldo completado: coldcare_20261115T165405Z.dump (1969199 bytes)`.
- Las dos ejecuciones se forzaron fuera de horario. La primera programada es el 16/11 a las
  02:00; la poda (retención de 14) aún no se ha ejercido.
- Backups nativos de Railway: el plan Trial no los ofrece. Queda documentado (B1).

## Escenario 2: simulacro de restauración

15/11, 11:12–11:14, despliegue `f2155204` del cron, contra un PostgreSQL temporal y aislado
(`Postgres-UBqY`), borrado después junto con su volumen. Resultado de
`scripts.verificar_restauracion` (`SALIDA=0`):

- Manifiesto `coldcare_20261115T160051Z.manifiesto.json`, archivo
  `coldcare_20261115T160051Z.dump`.
- `archivo_integro: true`, `coincide: true`, `verificado_en: 2026-11-15T16:13:45.638863+00:00`.
- Log: `Restauración verificada: filas y huellas coinciden
  (/respaldos/coldcare_20261115T160051Z.restauracion.json)`.

| Tabla | Filas origen | Filas restauradas | Filas | Huella |
|---|---:|---:|---|---|
| `ai_model_versions` | 0 | 0 | coinciden | coincide |
| `audit_logs` | 141 | 141 | coinciden | coincide |
| `corrective_actions` | 0 | 0 | coinciden | coincide |
| `device_config_history` | 1 | 1 | coinciden | coincide |
| `devices` | 1 | 1 | coinciden | coincide |
| `thermal_alerts` | 1 | 1 | coinciden | coincide |
| `thermal_readings` | 6978 | 6978 | coinciden | coincide |
| `traceability_records` | 13018 | 13018 | coinciden | coincide |
| `users` | 3 | 3 | coinciden | coincide |

La tabla se transcribió del log de despliegue. Railway muestra desordenadas las líneas que
llevan el mismo segundo, así que las filas de `device_config_history` (1) y `users` (3) se
asignaron por el orden alfabético de las claves del JSON, y no por la línea en que aparecen.
Los valores de `coincide` y `archivo_integro` no dependen de ese orden. El `.restauracion.json` y el
manifiesto originales siguen en el volumen del cron, junto al volcado. Para archivarlos aquí
sin el `.dump`, que contiene datos personales:

```bash
railway volume files download /coldcare_20261115T160051Z.restauracion.json docs/evidencias/HU-29/
railway volume files download /coldcare_20261115T160051Z.manifiesto.json docs/evidencias/HU-29/
```

**Próximo simulacro:** a más tardar el 29/11/2026 (cierre del sprint siguiente) y, en todo caso,
antes de cada entrega, como pide `docs/operacion/RESPALDO_RAILWAY.md` §3.

## Escenario 3: fallo del respaldo

- 11:08, con `PG_MAJOR=15` a propósito: `CRITICAL respaldo RESPALDO FALLIDO: pg_dump 15 es
  anterior al servidor PostgreSQL 18: construya Dockerfile.respaldo con PG_MAJOR=18` y la fila
  `RESPALDO_FALLIDO`. Esa fila es de prueba y queda en la bitácora.
- 11:46:18, en el log del Backend: `ALERTA OPERATIVA: el último respaldo programado de
  PostgreSQL falló (2026-11-15T16:08:31.321223+00:00) y no hay uno correcto posterior. Revise
  el servicio cron de respaldo (HU-29).`
- Aviso de la plataforma: la cuenta de Railway tiene reglas para todos los proyectos que avisan por
  correo y en la aplicación de *Deployment Failed* y *Deployment Crashed*. Una ejecución del cron
  que termina con error cuenta como *Deployment crashed*: el 15/11 la campana de Railway mostraba
  dos avisos «respaldo-postgres deployment crashed» (de hace unas 5 h al revisarla a las 16:56),
  que coinciden con las ejecuciones fallidas de prueba. Railway no tiene un evento propio para cron. Capturas: `HU-29_avisos_railway_cron_crashed.png` y
  `HU-29_reglas_avisos_railway.png`.
- Límite conocido: un fallo que no alcanza la BD (como el de `HOME`, corregido en Backend#36)
  no escribe `RESPALDO_FALLIDO`. La API lo detecta solo por antigüedad (26 h), pero el aviso de
  Railway sí sale porque el proceso termina con error.

## Pendiente

- Comprobar la primera ejecución programada (16/11, después de las 02:00) y que el vigilante
  muestre `respaldos.estado = null` en `/health/detalle`.
- Copiar a esta carpeta el `.restauracion.json` y el manifiesto originales (comandos de arriba).
- Riesgo de continuidad: la BD y sus respaldos están en la misma cuenta Trial. Conviene una
  copia periódica fuera de Railway, cifrada, porque el volcado lleva datos personales.
