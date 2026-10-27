# ColdCare Backend

Servicio backend del sistema de monitoreo de cadena de frío (FastAPI + PostgreSQL + MQTT/TLS) y contrato de
mensajes con el nodo ColdCare1023/IoT. Es la API que consume ColdCare1023/Frontend.

| Carpeta | Contenido |
|---|---|
| `src/` | Servicio: dominio, casos de uso, infraestructura (MQTT, base de datos, IA, PDF) e interfaz (API REST, SSE) |
| `alembic/` | Migraciones de PostgreSQL |
| `tests/` | Pruebas unitarias y de integración (`pytest`) |
| `scripts/` | `seed_dev.py` (usuarios de prueba) y simulador |
| [`contrato/`](contrato/README.md) | Contrato v1.0 nodo ↔ backend (`TelemetriaV1`, `EventoV1`, acuse) y ejemplos del firmware |
| [`banco_mqtt_local/`](banco_mqtt_local/README.md) | Banco mosquitto/TLS y backend simulado para probar el firmware aislado |
| [`simulacion_excursiones/`](simulacion_excursiones/README.md) | Excursiones simuladas para la demostración |
| [`infra-local/`](infra-local/README.md) | PostgreSQL + EMQX (TLS, ACL) + backend en Docker, para probar todo en una PC |
| `docs/evidencias/` | Evidencia de QA por historia de usuario |
| [`docs/ml/`](docs/ml/) | Modelo preventivo Random Forest: auditoría de datos, metodología, resultados, integración, reproducibilidad y alcance del entrenamiento (`10_TRAINING_EXTENT_AND_BEST_SCENARIO.md`) |

## Ejecutar

```bash
python -m venv .venv && .venv/Scripts/activate      # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                                 # ajustar DATABASE_URL, MQTT_*, JWT_SECRET_KEY
alembic upgrade head
python -m scripts.seed_dev
uvicorn src.interface.main:app --reload
pytest
```

Todo el stack (PostgreSQL, EMQX con TLS y el backend) en local: ver [`infra-local/`](infra-local/README.md).

## Dependencias

`requirements.in` y `requirements-dev.in` declaran las dependencias directas.
`requirements.txt` y `requirements-dev.txt` son lockfiles generados: versiones
exactas y hashes SHA-256, resueltos para todas las plataformas (`--universal`).
Al contener hashes, `pip install -r requirements.txt` (Railway, Docker, CI)
instala exactamente lo probado y rechaza cualquier paquete alterado.

Tras editar un `.in`, regenerar ambos lockfiles y auditar:

```bash
uv pip compile requirements.in --universal --generate-hashes --python-version 3.13 -o requirements.txt
uv pip compile requirements-dev.in --universal --generate-hashes --python-version 3.13 -c requirements.txt -o requirements-dev.txt
pip-audit --require-hashes --disable-pip -r requirements-dev.txt
```

Para actualizar un paquete concreto: añadir `--upgrade-package <nombre>` a los
dos comandos de compilación.

## Despliegue en Railway (HU-55; «HU-56» en la numeración antigua del código)

Guía completa y evidencia: [docs/operacion/HU-55_despliegue_railway.md](docs/operacion/HU-55_despliegue_railway.md).
Equivalencias de numeración: [docs/EQUIVALENCIA_HU_v3.3.md](docs/EQUIVALENCIA_HU_v3.3.md).

La imagen es [`infra-local/backend/Dockerfile`](infra-local/backend/Dockerfile) (la misma que usa
`infra-local/`: dependencias con hash, usuario sin privilegios) y la configuración del servicio está en
[`railway.json`](railway.json). Las migraciones corren como *pre-deploy* (`alembic upgrade head`): si fallan,
Railway no reemplaza la versión en marcha.

1. Crear el proyecto en Railway con un servicio **PostgreSQL** y un servicio desde este repositorio (rama `main`).
2. En el servicio del backend, cargar las variables de la tabla de abajo. `DATABASE_URL` se toma como referencia
   del servicio PostgreSQL (`${{Postgres.DATABASE_URL}}`); su forma `postgresql://` se normaliza sola a asyncpg.
3. Generar el dominio público del servicio y añadirlo a `ALLOWED_HOSTS` junto con `healthcheck.railway.app`
   (el healthcheck de Railway llega con ese `Host`; sin él, el despliegue nunca queda *healthy*).
4. Desplegar. Railway ejecuta `alembic upgrade head` (pre-deploy) y, solo si termina bien, arranca uvicorn en
   `$PORT`; `/health` responde en cuanto la API está lista.
5. En el frontend: `node scripts/generar-vercel-json.mjs https://<backend>.up.railway.app` (abre la CSP a la API
   y compila sin modo demo) y `VITE_API_BASE_URL=https://<backend>.up.railway.app` en Vercel.

Para probar la configuración de producción antes de subirla: `docker compose --profile cloud-like up -d --build`
en `infra-local/` (backend en `http://localhost:8001`, mismos validadores que en Railway).

`GET /health` es solo *liveness* (no consulta la base de datos, para que una caída de PostgreSQL no provoque
reinicios en bucle). `GET /health/detalle` (solo administrador) informa base de datos, revisión de Alembic,
estado de la conexión MQTT y la cola de notificaciones.

### Topología: 1 réplica y 1 worker, por diseño

`railway.json` fija `numReplicas: 1` y el `Dockerfile` arranca uvicorn con `--workers 1`. Varias piezas viven
en la memoria del proceso y no se comparten entre instancias:

| Estado en memoria | Para qué |
|---|---|
| `SSEBroadcaster` | Eventos en tiempo real del dashboard (lecturas, `conectividad`) |
| `JtiStore` | Revocación de JWT (logout) y tickets SSE de un solo uso |
| `SlidingWindowRateLimiter` | Límite de intentos de login y de solicitudes por IP |
| `DespachadorNotificaciones` | Cola, worker y reintentos de los avisos por correo (HU-53) |
| Cliente MQTT | `client_id` fijo: dos instancias se expulsarían mutuamente del broker |

**Limitación declarada:** un reinicio vacía ese estado. Lo relevante es que un token cerrado con logout vuelve a
valer hasta que expire (60 min). Los avisos de correo pendientes NO se pierden: al arrancar se recuperan los
episodios críticos abiertos aún sin notificar (`thermal_alerts.notificada_en IS NULL`). Escalar a varias
instancias exigiría un almacén compartido (p. ej. Redis), fuera del alcance del prototipo.

### Variables de entorno

«Prod» = el arranque falla en `ENVIRONMENT=production` si falta o es inválida (`src/infrastructure/config.py`).

| Variable | Prod | Ejemplo | Nota |
|---|---|---|---|
| `ENVIRONMENT` | — | `production` | Activa los validadores de producción |
| `DATABASE_URL` | sí | `${{Postgres.DATABASE_URL}}` | Se normaliza a `postgresql+asyncpg://` (`sslmode` → `ssl`) |
| `JWT_SECRET_KEY` | sí | 64 hex | ≥ 32 bytes |
| `ALLOWED_HOSTS` | sí | `["coldcare-api.up.railway.app","healthcheck.railway.app"]` | JSON |
| `CORS_ORIGINS` | sí | `["https://coldcare.vercel.app"]` | Solo `https://` |
| `MQTT_ENABLED` | — | `true` | |
| `MQTT_HOST`, `MQTT_PORT` | sí si MQTT | `xxxx.emqxsl.com`, `8883` | |
| `MQTT_USERNAME`, `MQTT_PASSWORD` | sí si MQTT | `backend_service` | |
| `MQTT_TLS_ENABLED` | sí si MQTT | `true` | |
| `MQTT_CA_FILE` | — | *(vacío)* | Vacío con EMQX Cloud (CA pública) |
| `DEVICE_REGISTRY_ESTRICTO` | — | `true` | Solo dispositivos dados de alta |
| `EMAIL_ENABLED` | — | `true` | `SMTP_ENABLED` se acepta como sinónimo |
| `EMAIL_TRANSPORTE` | — | `http_api` | `smtp` o `http_api` |
| `EMAIL_FROM` | sí si correo | `ColdCare <avisos@tu-dominio>` | `SMTP_FROM` como respaldo |
| `EMAIL_DESTINO_RESPALDO` | sí si correo | `qf@farmacia.pe` | Si el dispositivo no tiene `responsable_email`; `SMTP_TO` como respaldo |
| `FRONTEND_URL` | sí si correo | `https://coldcare.vercel.app` | Enlace a la alerta en el correo |
| `EMAIL_API_URL`, `EMAIL_API_KEY` | sí si `http_api` | `https://api.resend.com/emails`, `re_…` | API compatible con Resend |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | sí si `smtp` | `smtp.gmail.com`, `587` | |
| `SMTP_SEGURIDAD` | — | `starttls` | `ssl` (465) o `starttls` (587) |
| `NOTIFICACION_MAX_INTENTOS` | — | `3` | Reintentos con espera de 30 s, 2 min y 5 min |
| `NOTIFICACION_COOLDOWN_MINUTOS` | — | `15` | Antiflood de Telegram/SMS (HU-54); el correo se deduplica por episodio |
| `ALERTA_VENTANA_NORMALIZACION_MINUTOS` | — | `15` | HU-20: reabrir dentro de la ventana es el mismo episodio (sin correo nuevo) |
| `TELEGRAM_*`, `SMS_*` | si se habilitan | | Canales opcionales (HU-54) |
| `GOOGLE_OAUTH_ENABLED`, `GOOGLE_CLIENT_ID` | si se habilita | | RF-17 |
