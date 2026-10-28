# HU-55 — Despliegue del backend en Railway

Sin este despliegue ninguna HU cumple la Definición de Hecho (Charter, TI
§1.5.4, OE2.I1 §2.2). El repositorio deja lista la configuración; el
despliegue y la evidencia se hacen en la cuenta de Railway del equipo.

## Configuración que ya fija el repositorio

| Requisito | Dónde |
|---|---|
| Imagen con Python 3.13 y `postgresql-client` | `infra-local/backend/Dockerfile` |
| `alembic upgrade head` antes de arrancar | `railway.json` → `preDeployCommand` |
| 1 réplica, 1 worker, healthcheck `/health` | `railway.json`, `CMD` del Dockerfile |
| Sin suspensión por inactividad | `railway.json` → `sleepApplication: false` |
| `DATABASE_URL` `postgres://` → `postgresql+asyncpg://` | `Settings._normalizar_database_url` |
| Cabeceras del proxy (`--proxy-headers`, `--forwarded-allow-ips`) | `CMD` del Dockerfile |

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
aplicadas, MQTT conectado, el proxy entrega `https` y `/docs` no está publicado.
Adjunte también la captura de *Settings → Deploy* con «Serverless» desactivado.
