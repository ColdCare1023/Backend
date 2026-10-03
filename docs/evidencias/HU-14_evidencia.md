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
