# HU-55 — Despliegue del backend en Railway

Sin este despliegue ninguna HU cumple la Definición de Hecho (Charter, TI
§1.5.4, OE2.I1 §2.2). El repositorio deja lista la configuración; el
despliegue y la evidencia se hacen en la cuenta de Railway del equipo.

## Configuración que ya fija el repositorio

| Requisito | Dónde |
|---|---|
| Imagen con Python 3.12 y `postgresql-client` | `infra-local/backend/Dockerfile` |
| `alembic upgrade head` antes de arrancar | `railway.json` → `preDeployCommand` y el panel (*Pre-deploy Command*) |
| 1 réplica, healthcheck `/health` | `railway.json` y el panel (*Replicas*, *Healthcheck Path*) |
| 1 worker, `$PORT`, cabeceras del proxy (`--proxy-headers`, `--forwarded-allow-ips`) | `scripts/arrancar_api.py`, al que apuntan el `CMD` del Dockerfile, `railway.json`, `railpack.json` y el `Procfile` |
| Sin suspensión por inactividad | `railway.json` → `sleepApplication: false` y el panel (*Serverless* desactivado) |
| `DATABASE_URL` `postgres://` → `postgresql+asyncpg://` | `Settings._normalizar_database_url` |

## Builder: Dockerfile o Railpack

Hasta el 15/11/2026 el panel decía Railpack, pero Railway aplicaba el
`railway.json` de la raíz: el log de build muestra `load build definition from
infra-local/backend/Dockerfile`, el *pre-deploy* corre `alembic upgrade head` y
el proceso es uno solo (PID 1 `python -m scripts.arrancar_api`). Railway deja
de leer config-as-code el **2026-12-01**. Desde esa fecha solo vale lo que diga
el panel, así que el 15/11 se cargó en el panel del servicio Backend lo que fija
`railway.json` (los cambios de builder, ruta, comando de arranque, timeout y
reintentos quedaron preparados hasta pulsar *Deploy Changes*):

| Panel (*Settings*) | Valor |
|---|---|
| Build → Builder | Dockerfile |
| Build → Dockerfile Path | `infra-local/backend/Dockerfile` |
| Deploy → Pre-deploy Command | `alembic upgrade head` |
| Deploy → Custom Start Command | `python -m scripts.arrancar_api` |
| Deploy → Healthcheck Path / Timeout | `/health` / 120 s |
| Deploy → Serverless | desactivado |
| Deploy → Restart Policy | On Failure, 5 reintentos |
| Scale → Replicas | 1 |

Los cambios del panel se aplican con *Deploy Changes*, que redespliega el
servicio. Mientras exista `railway.json`, el resultado es el mismo. Después del
corte, si el panel volviera a Railpack, el servicio solo recibiría el
`startCommand` (de `RAILPACK_START_CMD`, `railpack.json` o el `Procfile`, que
llevan el mismo comando) y perdería el resto. Railpack tampoco instala
`postgresql-client`, que hace falta para `pg_dump` (HU-29).
`/health/detalle` → `arranque` muestra con qué comando arrancó el proceso:
`{"lanzador": "scripts.arrancar_api", "workers": 1}`, o `null` si fue otro.
El cron de respaldo (`respaldo-postgres`) ya tiene en su panel `Dockerfile.respaldo`,
`0 7 * * *` y restart Never (ver `RESPALDO_RAILWAY.md`).

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
| `IA_PREVENTIVO_MODO` | `sombra` (criterios del 20/11/2026, HU-34 y HU-46): predice y guarda evidencia sin cambiar el riesgo efectivo ni abrir alertas; el tablero la rotula «IA en modo sombra» |
| `IA_PREVENTIVO_VERSION`, `IA_PREVENTIVO_SHA256` | `rf-preventivo-h30-termico-puerta-1.0.0` y `6d5c81c0403f5c96b70accc1b079f6ff6760e45e7cf83abdc12f4c7d49f6aaa6` (SHA-256 del `modelo.joblib` de esa versión) |
| `FORWARDED_ALLOW_IPS` | ver «Proxy» |
| `SSE_MAX_STREAMS_POR_USUARIO` | opcional, por defecto `10` (flujos SSE por cuenta; pasado el tope, 429 `demasiados_flujos`) |
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
