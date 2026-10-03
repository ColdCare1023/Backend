# Evidencia — HU-22: Persistencia del histórico

Entorno: `infra-local/` (PostgreSQL 16, EMQX 5.8.6 con TLS 8883 y CA propia, backend FastAPI en Docker), nodo ESP32 DevKitC real con firmware ColdCare1023/IoT `develop` (v1.2.0) salvo donde se indique, frontend ColdCare1023/Frontend `main`. Fechas: 2–3 de octubre de 2026.

> Reemplaza la evidencia anterior de esta HU, que se apoyaba en pruebas sobre módulos simulados (cliente MQTT sin conexión real, broker y navegador falsos en Python) y no ejercitaba el sistema real.
## Defectos corregidos (solo aparecían en PostgreSQL; las pruebas usaban SQLite)
1. Varias revisiones de Alembic tienen ids de más de 32 caracteres y `alembic_version.version_num` es
   `VARCHAR(32)`: `value too long` al migrar.
2. La migración 0008 comparaba un booleano con un entero (`revisada = 1`): `operator does not exist`.

## Resultados en vivo (PostgreSQL 16.14)
| Tarea | Resultado |
|---|---|
| [BE] Persistencia en PostgreSQL real | ✅ Migraciones aplicadas desde cero; lecturas del ESP32 persistidas |
| [BE] Idempotencia | ✅ Misma lectura 3 veces → 3 acuses, 1 fila |
| [QA] Solo persiste lo validado | ✅ Humedad 150 % → `rejected`, 0 filas |
