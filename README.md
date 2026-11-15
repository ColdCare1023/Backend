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
uv pip compile requirements.in --universal --generate-hashes --python-version 3.12 -o requirements.txt
uv pip compile requirements-dev.in --universal --generate-hashes --python-version 3.12 -c requirements.txt -o requirements-dev.txt
pip-audit --require-hashes --disable-pip -r requirements-dev.txt
```

Para actualizar un paquete concreto: añadir `--upgrade-package <nombre>` a los
dos comandos de compilación.

## Despliegue en Railway (HU-55)

Guía completa y evidencia: [docs/operacion/HU-55_despliegue_railway.md](docs/operacion/HU-55_despliegue_railway.md).
Equivalencias con la numeración anterior, la que usan las evidencias antiguas: [docs/EQUIVALENCIA_HU_v3.3.md](docs/EQUIVALENCIA_HU_v3.3.md).

La imagen es [`infra-local/backend/Dockerfile`](infra-local/backend/Dockerfile) (la misma que usa
`infra-local/`: dependencias con hash, usuario sin privilegios) y la configuración del servicio está en
[`railway.json`](railway.json). Las migraciones corren como *pre-deploy* (`alembic upgrade head`): si fallan,
Railway no reemplaza la versión en marcha.

### Pasos

1. Crear el proyecto en Railway con un servicio **PostgreSQL** y un servicio desde este repositorio (rama `main`).
2. En el servicio del backend, cargar las variables de la tabla de abajo. `DATABASE_URL` se toma como referencia
   del servicio PostgreSQL (`${{Postgres.DATABASE_URL}}`); su forma `postgresql://` se normaliza sola a asyncpg.
3. Generar el dominio público del servicio y añadirlo a `ALLOWED_HOSTS` junto con `healthcheck.railway.app`
   (el healthcheck de Railway llega con ese `Host`; sin él, el despliegue nunca queda *healthy*).
4. En *Settings → Deploy*: *Pre-deploy Command* `alembic upgrade head`, *Healthcheck Path* `/health`,
   *Replicas* 1, *Serverless* (apagado por inactividad) desactivado y *Custom Start Command* vacío o
   `python -m scripts.arrancar_api` (si el servicio aplica `railway.json`, todo esto ya viene de ahí).
5. Desplegar. Railway ejecuta `alembic upgrade head` (pre-deploy) y, solo si termina bien, arranca
   `python -m scripts.arrancar_api` en `$PORT`; `/health` responde en cuanto la API está lista.
6. En el frontend: `node scripts/generar-vercel-json.mjs https://<backend>.up.railway.app` (abre la CSP a la API
   y compila sin modo demo) y `VITE_API_BASE_URL=https://<backend>.up.railway.app` en Vercel.
7. Dejar la evidencia: `BACKEND_URL=https://<backend>.up.railway.app EVIDENCIA_TOKEN=<JWT de administrador>
   python -m scripts.evidencia_despliegue --salida docs/evidencias/HU-55_despliegue.json`. Sale con 0 solo si
   todo está bien (HTTPS, `/health`, base de datos, migraciones, MQTT, 1 worker con el lanzador, proxy con
   `https` e IP pública, `/docs` oculto y el mismo comando en las cuatro vías del repositorio). Sin red o sin
   token no lanza una traza: lo deja escrito en el JSON y sale con 1. El token nunca se escribe en el JSON.

### Arranque: un solo comando para cualquier builder

```bash
python -m scripts.arrancar_api
```

[`scripts/arrancar_api.py`](scripts/arrancar_api.py) es el único lugar que fija cómo arranca uvicorn en
producción: `src.interface.main:app` en `0.0.0.0:$PORT` (8000 si no hay `PORT`), **1 worker** (aunque exista
`WEB_CONCURRENCY`) y `--proxy-headers` con `--forwarded-allow-ips` tomado de `FORWARDED_ALLOW_IPS` (por
defecto `127.0.0.1`). El mismo comando está en las cuatro vías de despliegue, y
`tests/unit/test_hu55_arranque.py` falla si alguna se desalinea:

| Vía | Archivo | Cuándo la usa Railway |
|---|---|---|
| Dockerfile | `CMD` de `infra-local/backend/Dockerfile` | Builder = Dockerfile |
| Config-as-code | `deploy.startCommand` de `railway.json` | Si el servicio aplica `railway.json` |
| Railpack | `deploy.startCommand` de `railpack.json` | Builder = Railpack (sin comando en el panel) |
| Procfile | `web:` de `Procfile` | Railpack, si no hay `railpack.json` |

**Nota sobre Railpack.** El servicio de producción construye con Railpack, que no usa el `CMD` del
Dockerfile, y en la práctica tampoco aplicó `railway.json`: el *pre-deploy* `alembic upgrade head` y el
healthcheck `/health` se configuraron a mano en el panel (ver `docs/operacion/HU-55_despliegue_railway.md`).
Railpack toma el comando de arranque, en este orden, de la variable `RAILPACK_START_CMD`, de `railpack.json`
y del `Procfile`; un *Custom Start Command* escrito en el panel de Railway los reemplaza a todos. Deje ese
campo vacío (o con `python -m scripts.arrancar_api`) y compruébelo en `/health/detalle` → `arranque`: debe
decir `{"lanzador": "scripts.arrancar_api", "workers": 1}`. `null` significa que el proceso arrancó con otro
comando. Railpack no instala `postgresql-client`: el respaldo programado (HU-29) necesita la imagen del
Dockerfile.

**IP real del cliente.** Railway termina TLS en su borde y reenvía con `X-Forwarded-For` y
`X-Forwarded-Proto`. Uvicorn solo confía en esas cabeceras si la conexión viene de una IP de
`FORWARDED_ALLOW_IPS`; con el valor por defecto (`127.0.0.1`) el backend ve la IP interna del proxy y
`http`. Entonces la cuota de intentos de login por IP (HU-39) la comparten todos los clientes y la bitácora
(HU-42/HU-50) registra la IP del proxy. Defina `FORWARDED_ALLOW_IPS` con la red interna del proxy (la IP que
aparece en `/health/detalle` → `proxy.ip_cliente` con el valor por defecto, p. ej. `100.64.0.0/10`) y
compruebe que `proxy.esquema` sea `https` y `proxy.ip_cliente` su IP pública. No use `*` salvo que haya
verificado que el borde reemplaza (no anexa) el `X-Forwarded-For` del cliente: con `*` la IP se podría
falsificar.

Para probar la configuración de producción antes de subirla: `docker compose --profile cloud-like up -d --build`
en `infra-local/` (backend en `http://localhost:8001`, mismos validadores que en Railway).

`GET /health` es solo *liveness* (no consulta la base de datos, para que una caída de PostgreSQL no provoque
reinicios en bucle). `GET /health/detalle` (solo administrador) informa base de datos, revisión de Alembic
(`alembic_revision`) y la última migración que trae el código desplegado (`alembic_head`; si no coinciden, el
*pre-deploy* `alembic upgrade head` no se aplicó y `scripts.evidencia_despliegue` lo marca), estado de la conexión MQTT, la cola de notificaciones, `arranque` (lanzador y workers), `proxy` (esquema e IP
que ve el backend) y `modelo_ia` (HU-16): si el modelo está disponible,
su versión, el motivo del rechazo cuando el artefacto no supera la verificación (checksum, hash de la versión
activa o compatibilidad), la versión de scikit-learn con que se entrenó frente a la instalada y el modo del
modelo preventivo. Un artefacto rechazado deja `status` en `degradado`; la ingesta y la regla 2-8 °C siguen.

### Topología: 1 réplica y 1 worker, por diseño

`railway.json` fija `numReplicas: 1` (en el panel: *Replicas* 1) y `scripts/arrancar_api.py` arranca uvicorn con
1 worker. Varias piezas viven en la memoria del proceso y no se comparten entre instancias:

| Estado en memoria | Para qué |
|---|---|
| `SSEBroadcaster` y `sse_streams_por_usuario` | Eventos en tiempo real del dashboard (lecturas, `conectividad`) y el tope de flujos SSE por cuenta |
| `SlidingWindowRateLimiter` | Límite de intentos de login y de solicitudes por IP |
| `DespachadorNotificaciones` | Cola, worker y reintentos de los avisos por correo (HU-53) |
| Cliente MQTT | `client_id` fijo: dos instancias se expulsarían mutuamente del broker |

**Limitación declarada:** un reinicio vacía ese estado: se cierran los flujos SSE (el dashboard reconecta solo)
y se reinician los contadores de intentos por IP. La revocación de JWT (logout, desactivación, cambio de rol) y
el consumo de los tickets SSE NO están en memoria: viven en la tabla `jti_invalidados` (BE-009) y sobreviven al
reinicio. Los avisos de correo pendientes tampoco se pierden: al arrancar se recuperan los episodios críticos
abiertos aún sin notificar (`thermal_alerts.notificada_en IS NULL`). Escalar a varias instancias exigiría un
almacén compartido (p. ej. Redis) para el resto, fuera del alcance del prototipo.

**Reconexión MQTT (HU-12 esc. 3).** Si el broker cae, la ingesta reintenta con espera creciente (1, 2, 4… hasta
60 s) mientras no logre conectar. Una sesión que llegó a conectar reinicia la espera a 1 s: un corte breve del
broker no deja la ingesta parada un minuto aunque haya habido cortes antes.

### Variables de entorno

«Prod» = el arranque falla en `ENVIRONMENT=production` si falta o es inválida (`src/infrastructure/config.py`).

| Variable | Prod | Ejemplo | Nota |
|---|---|---|---|
| `ENVIRONMENT` | — | `production` | Activa los validadores de producción |
| `DATABASE_URL` | sí | `${{Postgres.DATABASE_URL}}` | Se normaliza a `postgresql+asyncpg://` (`sslmode` → `ssl`) |
| `JWT_SECRET_KEY` | sí | 64 hex | ≥ 32 bytes |
| `TRAZABILIDAD_HMAC_KEY` | sí | 64 hex | Clave HMAC de la cadena de trazabilidad (eslabones v3). ≥ 32 bytes, distinta de `JWT_SECRET_KEY`; fuera de la base de datos. Sin ella los eslabones v3 no se pueden verificar. Ver [docs/operacion/HASH_CANONICO.md](docs/operacion/HASH_CANONICO.md) |
| `FORWARDED_ALLOW_IPS` | — | `100.64.0.0/10` | Red del proxy de Railway en la que confía uvicorn (`--forwarded-allow-ips`). Por defecto `127.0.0.1`: el backend vería la IP del proxy. Ver «IP real del cliente» |
| `PORT` | — | *(lo asigna Railway)* | Puerto de uvicorn; 8000 si falta |
| `ALLOWED_HOSTS` | sí | `["coldcare-api.up.railway.app","healthcheck.railway.app"]` | JSON |
| `CORS_ORIGINS` | sí | `["https://coldcare.vercel.app"]` | Solo `https://` |
| `MQTT_ENABLED` | — | `true` | |
| `MQTT_HOST`, `MQTT_PORT` | sí si MQTT | `xxxx.emqxsl.com`, `8883` | |
| `MQTT_USERNAME`, `MQTT_PASSWORD` | sí si MQTT | `backend_service` | |
| `MQTT_TLS_ENABLED` | sí si MQTT | `true` | |
| `MQTT_CA_FILE` | — | *(vacío)* | Vacío con EMQX Cloud (CA pública) |
| `DEVICE_REGISTRY_ESTRICTO` | — | `true` | Solo dispositivos dados de alta |
| `EMAIL_ENABLED` | — | `true` | `SMTP_ENABLED` se acepta como sinónimo |
| `EMAIL_TRANSPORTE` | — | `apps_script` | `smtp`, `http_api` (API compatible con Resend) o `apps_script` (relé propio en Google Apps Script; es el de producción) |
| `EMAIL_API_URL`, `EMAIL_API_KEY` (con `apps_script`) | sí si `apps_script` | `https://script.google.com/macros/s/…/exec`, secreto ≥ 32 caracteres | URL `/exec` del despliegue web del relé y secreto compartido (el mismo valor que la propiedad `SECRETO` del script). Ver [docs/operacion/CODIGOS_VERIFICACION.md](docs/operacion/CODIGOS_VERIFICACION.md) |
| `EMAIL_FROM_NOMBRE` | — | `ColdCare` | Nombre visible del remitente en el relé de Apps Script |
| `EMAIL_FROM` | sí si correo | `ColdCare <avisos@tu-dominio>` | `SMTP_FROM` como respaldo |
| `EMAIL_DESTINO_RESPALDO` | sí si correo | `qf@farmacia.pe` | Si el dispositivo no tiene `responsable_email`; `SMTP_TO` como respaldo |
| `FRONTEND_URL` | sí si correo | `https://coldcare.vercel.app` | Enlace a la alerta en el correo y base de las páginas de código (`/recuperar-contrasena`) |
| `PASSWORD_RESET_URL` | sí si correo (HTTPS) | `https://coldcare.vercel.app/restablecer-password` | Página del enlace de recuperación heredado (`#token=`). Si queda en su valor de desarrollo, se usa `FRONTEND_URL/restablecer-password` |
| `OTP_LOGIN_ENABLED` | — | `true` | Segundo factor del login con contraseña (código de 6 dígitos por correo). Exige el correo habilitado. `false` es el interruptor de emergencia si el correo deja de salir |
| `CODIGOS_HMAC_KEY` | sí si correo u OTP | 64 hex | Clave HMAC de los códigos de verificación guardados. ≥ 32 bytes, distinta de `JWT_SECRET_KEY` y de `TRAZABILIDAD_HMAC_KEY` |
| `EMAIL_API_URL`, `EMAIL_API_KEY` | sí si `http_api` | `https://api.resend.com/emails`, `re_…` | API compatible con Resend |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | sí si `smtp` | `smtp.gmail.com`, `587` | |
| `SMTP_SEGURIDAD` | — | `starttls` | `ssl` (465) o `starttls` (587) |
| `NOTIFICACION_MAX_INTENTOS` | — | `3` | Reintentos con espera de 30 s, 2 min y 5 min |
| `NOTIFICACION_COOLDOWN_MINUTOS` | — | `15` | Antiflood de Telegram; el correo se deduplica por episodio |
| `ALERTA_VENTANA_NORMALIZACION_MINUTOS` | — | `15` | HU-20: reabrir dentro de la ventana es el mismo episodio (sin correo nuevo). HU-21 esc. 3: una alerta ya ATENDIDA nunca se reabre; la nueva excursión es otra alerta PENDIENTE, con su correo |
| `ALERTA_PREVENTIVA_MINUTOS_NORMALES` | — | `10` | HU-20: minutos seguidos en Normal para resolver una alerta preventiva; `0` la cierra con la primera lectura normal |
| `TELEGRAM_*` | si se habilita | | Canal opcional |
| `SMS_*` | — | | **Retiradas** (HU-53 fuera del alcance): se ignoran sin impedir el arranque; se pueden borrar del entorno |
| `BROKER_API_URL`, `BROKER_API_KEY`, `BROKER_API_SECRET` | — | `https://…/api/v5` | API de administración de EMQX: expulsa sesiones y, en modo `api`, crea y retira las credenciales de los nodos. Cada resultado queda en la bitácora (`BROKER_OPERACION_SINCRONIZADA`, `BROKER_OPERACION_PENDIENTE`, `BROKER_OPERACION_FALLIDA`) |
| `BROKER_USUARIOS_MODO` | — | `manual` | `manual` en EMQX Serverless (usuarios a mano en la consola, pendiente visible); `api` en EMQX 5 completo o Dedicated |
| `RESPALDO_MAX_ANTIGUEDAD_HORAS` | — | `0` | HU-29 esc. 3: con N > 0, el backend deja una alerta `CRITICAL` en su log si en N h no hubo ningún `RESPALDO_COMPLETADO` (recomendado 26 una vez creado el cron). Un `RESPALDO_FALLIDO` posterior al último completado siempre la deja. Periodo de revisión: `RESPALDO_VIGILANCIA_PERIODO_SEGUNDOS` (3600) |
| `SSE_MAX_STREAMS_POR_USUARIO` | — | `10` | Flujos SSE simultáneos por cuenta (pestañas o dispositivos con el dashboard abierto; 1 a 100). Pasado el tope, `POST /api/auth/sse-ticket` y `GET /api/sse/lecturas` responden 429 con `detail: "demasiados_flujos"` y `Retry-After` |
| `GOOGLE_OAUTH_ENABLED`, `GOOGLE_CLIENT_ID` | si se habilita | | RF-17 |
