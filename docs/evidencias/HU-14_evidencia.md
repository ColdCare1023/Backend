# Evidencia — HU-14: Uso exclusivo de endpoints cifrados

Entorno: `infra-local/` (PostgreSQL 16, EMQX 5.8.6 con TLS 8883 y CA propia, backend FastAPI en Docker), nodo ESP32 DevKitC real con firmware ColdCare1023/IoT `develop` (v1.2.0) salvo donde se indique, frontend ColdCare1023/Frontend `main`. Fechas: 2–3 de octubre de 2026.

## Configuración
EMQX con los listeners `tcp:default` (1883), `ws` (8083) y `wss` (8084) deshabilitados; solo `ssl:default`
en 8883 (`infra-local/docker-compose.yml`). El firmware no admite 1883 (puerto no configurable).

## Resultados
```
$ docker compose exec emqx emqx ctl listeners
tcp:default   listen_on: :1883   enable: false   running: false
ssl:default   listen_on: 0.0.0.0:8883   enable: true   running: true
ws:default    enable: false   running: false
wss:default   enable: false   running: false
```
`smoke_test.py`: `[OK ] 1883 cerrado (sin TLS no hay conexión)` (probado contra la IP LAN, que es la que
usa el nodo).

## Escenario 3: observación de intentos no autorizados (sin canal en texto plano)

Los intentos de conexión rechazados se consultan sin abrir el 1883 ni ningún listener sin TLS:

- **EMQX local (`infra-local/`)**: `docker compose exec emqx emqx ctl listeners` confirma que solo
  `ssl:default` (8883) está activo; los CONNECT rechazados por credenciales (CONNACK 0x04/0x05) quedan
  en el registro del broker (`docker compose logs emqx | grep -i "authentication failed\|not authorized"`)
  y en las métricas `authentication.failure` del panel de EMQX (18083).
- **EMQX Cloud Serverless (producción)**: en la consola de la instancia, *Monitor → Logs* lista los
  intentos de autenticación fallidos y *Overview* muestra las sesiones activas; la instancia solo
  expone MQTT sobre TLS (8883) y WebSocket sobre TLS (8084).
- **Backend**: la telemetría de un nodo revocado o no registrado se rechaza con acuse
  `dispositivo_no_autorizado` y queda en la bitácora (`TELEMETRIA_RECHAZADA_*`), consultable por el
  administrador en Auditoría; los fallos de inicio de sesión web aparecen en *Eventos de seguridad* (HU-50).

Pendiente para la DoD completa: adjuntar la captura de un intento fallido real en la consola de la
instancia desplegada.
