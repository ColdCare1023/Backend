# HU-55 — Despliegue del backend en Railway

Sin este despliegue ninguna HU cumple la Definición de Hecho (Charter, TI
§1.5.4, OE2.I1 §2.2). El repositorio deja lista la configuración; el
despliegue y la evidencia se hacen en la cuenta de Railway del equipo.

## Configuración que ya fija el repositorio

| Requisito | Dónde |
|---|---|
| Imagen con Python 3.12 y `postgresql-client` | `infra-local/backend/Dockerfile` |
| `alembic upgrade head` antes de arrancar | `railway.json` → `preDeployCommand` (con Railpack: a mano en el panel) |
| 1 réplica, healthcheck `/health` | `railway.json` (con Railpack: a mano en el panel) |
| 1 worker, `$PORT`, cabeceras del proxy (`--proxy-headers`, `--forwarded-allow-ips`) | `scripts/arrancar_api.py`, al que apuntan el `CMD` del Dockerfile, `railway.json`, `railpack.json` y el `Procfile` |
| Sin suspensión por inactividad | `railway.json` → `sleepApplication: false` (con Railpack: *Serverless* desactivado en el panel) |
| `DATABASE_URL` `postgres://` → `postgresql+asyncpg://` | `Settings._normalizar_database_url` |

## Builder: Dockerfile o Railpack

El servicio de producción se construye con **Railpack**. Railpack no usa el
`CMD` del Dockerfile y el servicio tampoco aplicó `railway.json`, así que el
*pre-deploy*, el healthcheck, las réplicas y el apagado por inactividad se
configuran en el panel (*Settings → Deploy*). El comando de arranque ya no
depende del panel: Railpack lo toma de `RAILPACK_START_CMD`, luego de
`railpack.json` y luego del `Procfile`, y los tres caminos del repositorio
llevan `python -m scripts.arrancar_api`. Lo único que lo reemplaza es un
*Custom Start Command* escrito en el panel: déjelo vacío o con ese mismo
comando. `/health/detalle` → `arranque` muestra con qué comando arrancó el
proceso (`{"lanzador": "scripts.arrancar_api", "workers": 1}`; `null` si fue
otro). Para el respaldo programado (HU-29, `pg_dump`) se necesita la imagen
del Dockerfile: Railpack no instala `postgresql-client`.

## Variables del servicio (Railway → Variables)

| Variable | Valor |
|---|---|
| `ENVIRONMENT` | `production` |
| `DATABASE_URL` | referencia `${{Postgres.DATABASE_URL}}` |
| `JWT_SECRET_KEY`, `TRAZABILIDAD_HMAC_KEY` | dos secretos aleatorios distintos de ≥ 32 bytes |
| `CORS_ORIGINS` | `["https://<frontend>.vercel.app"]` |
| `ALLOWED_HOSTS` | `["<servicio>.up.railway.app"]` |
| `MQTT_HOST`, `MQTT_PORT=8883`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_TLS_ENABLED=true` | instancia EMQX Cloud Serverless |
| `BROKER_API_URL`, `BROKER_API_KEY`, `BROKER_API_SECRET` | API key de la instancia (expulsión de sesiones). La URL incluye la ruta de la API; el backend la comprueba al arrancar (`GET /clients`) y lo muestra como `broker_api` en `/health/detalle` |
| `BROKER_USUARIOS_MODO` | `manual` (Serverless no tiene API de usuarios) |
| `IA_LEGADO_MODO` | `experimental` (o `desactivado`, decisión D-2) |
| `FORWARDED_ALLOW_IPS` | ver «Proxy» |
| `SSE_MAX_STREAMS_POR_USUARIO` | opcional, por defecto `3` (flujos SSE por cuenta; pasado el tope, 429 `demasiados_flujos`) |
| `CODIGOS_HMAC_KEY`, `OTP_LOGIN_ENABLED`, `PASSWORD_RESET_URL` | ver `docs/operacion/CODIGOS_VERIFICACION.md` y la tabla del README |
| `EMAIL_*` / `SMTP_*`, `FRONTEND_URL` | si se habilita el correo (HU-52) |

## Proxy (B-11)

Railway termina TLS en su borde y reenvía con `X-Forwarded-For` y
`X-Forwarded-Proto`. Tras desplegar, abra `/health/detalle` como
administrador y mire el bloque `proxy`:

- `esquema` debe ser `https`. Si sale `http`, el backend no confía en el proxy:
  defina `FORWARDED_ALLOW_IPS` con el rango de la red privada de Railway
  (el que aparezca como `ip_cliente` con el valor por defecto).
- `ip_cliente` debe ser la IP pública del navegador, no una IP interna.

No use `*` salvo que haya comprobado que el borde de Railway reemplaza (no
anexa) la cabecera `X-Forwarded-For` del cliente; si la anexa, `*` permitiría
falsificar la IP y saltarse el límite de intentos por IP (el límite por cuenta,
BE-008, sigue vigente igualmente).

## Evidencia

```bash
BACKEND_URL=https://<servicio>.up.railway.app EVIDENCIA_TOKEN=<JWT admin> \
  python -m scripts.evidencia_despliegue --salida docs/evidencias/HU-55_despliegue.json
```

Sale con 0 solo si: HTTPS, `/health` 200, base de datos ok, migraciones
aplicadas, MQTT conectado, el proceso arrancó con `scripts.arrancar_api` y 1
worker, el proxy entrega `https` y la IP pública del cliente, `/docs` no está
publicado y el Procfile, `railpack.json`, `railway.json` y el Dockerfile
llevan el mismo comando. Es seguro sin red o sin token: no lanza una traza,
lo deja escrito en el JSON (`error_conexion`) y sale con 1; el token nunca se
escribe en la evidencia. Adjunte también la captura de *Settings → Deploy*
con «Serverless» desactivado y *Replicas* 1.

## Primer administrador (H9)

En una base nueva no hay usuarios y `seed_dev` no puede correr con
`ENVIRONMENT=production`. Con el servicio ya desplegado y migrado, ejecute una
sola vez dentro del contenedor (`railway ssh`, o `railway connect` como túnel):

```bash
python -m scripts.crear_admin
```

Pide nombre, correo y contraseña (sin eco, con confirmación), aplica la política
de contraseñas de la API y se niega si ya existe un administrador activo. El
resto de cuentas (químico farmacéutico, auditor) se crean desde la interfaz.
