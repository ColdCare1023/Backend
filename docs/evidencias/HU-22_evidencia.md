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

## `boot_id` y `seq_no` como BIGINT (migración 0042)

El contrato v1.0 admite `boot_id` y `seq_no` hasta UINT32 (4 294 967 295),
pero en `thermal_readings` eran `INTEGER` de PostgreSQL (máximo
2 147 483 647). Una lectura con un valor entre ambos pasaba la validación y
fallaba ya en la consulta de idempotencia (`$2::INTEGER` fuera de rango): sin
COMMIT no había acuse y el nodo la reintentaba para siempre con su cola FIFO
bloqueada (esc. 3). La migración `0042_bigint_identidad` las pasa a `BIGINT`,
como ya lo eran `devices.conectividad_boot_id`, `estado_nodo_dispositivo.boot_id`
y los `*_boot_id`/`*_seq_no` de `episodios_buffer_saturado`. Es reversible: el
downgrade vuelve a `INTEGER` y se niega si alguna lectura ya no cabe.

Pruebas (PostgreSQL real): `tests/integration/test_hu22_identidad_bigint_postgres.py`.
