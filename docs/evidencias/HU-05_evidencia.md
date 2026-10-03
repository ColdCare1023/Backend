# Evidencia — HU-05: Estructuración versionada de telemetría (contrato v1.0)

Entorno: `infra-local/` (PostgreSQL 16, EMQX 5.8.6 con TLS 8883 y CA propia, backend FastAPI en Docker), nodo ESP32 DevKitC real con firmware ColdCare1023/IoT `develop` (v1.2.0) salvo donde se indique, frontend ColdCare1023/Frontend `main`. Fechas: 2–3 de octubre de 2026.

> Reemplaza la evidencia anterior de esta HU, que se apoyaba en pruebas sobre módulos simulados (cliente MQTT sin conexión real, broker y navegador falsos en Python) y no ejercitaba el sistema real.
## Defecto corregido
El backend validaba el contrato anterior (`schema_version: 1`, `timestamp`, `temperatura_interna`, …) con
`extra="forbid"`: **rechazaba el 100 % de los mensajes** del firmware ColdCare (contrato v1.0). Ahora
`src/infrastructure/mqtt/contrato_v1.py` valida `TelemetriaV1`/`EventoV1` (copia de `contrato/telemetria_v1.py`)
y los traduce al modelo interno; el acuse lleva `boot_id`, `seq_no` y `status`
(`committed`/`duplicate`/`rejected`), que es lo que parsea `core/Ack.cpp`.

## Tareas
| Tarea | Resultado |
|---|---|
| [FW] Campos del payload real | ✅ Los 9 ejemplos generados por el firmware (`tests/fixtures/contrato_v1/`) se persisten y acusan |
| [FW] Sensor caído como null + SENSOR_FAULT | ✅ DS18B20 desconectado en vivo: `temperatura_interna = NULL`, `ds18b20_fault = no_response`, IA omitida |
| [FW] Rechazo por tamaño excedido | ✅ Payload v1 > 768 B → acuse `rejected` |
| [BE] `received_at` en recepción | ✅ Columna `received_at` estampada por el backend en cada fila |
| [QA] Evidencia | ✅ 22 pruebas `test_contrato_v1_coldcare.py` + nodo real |

## Nodo real (ESP32_FARMACIA_01, firmware v1.2.0)
```
boot_id | seq_no | time_quality | t_int | t_amb |  hr   | puerta | contrato |   fw
     23 |      2 | synced       | 24.56 | 25.35 | 69.91 | f      | 1.0      | v1.2.0
     23 |      3 | synced       | 24.56 | 25.40 | 69.84 | f      | 1.0      | v1.2.0
```
Monitor serie: `[MQTT] Conectado (TLS, QoS 1). Acuses en farmacias/ESP32_FARMACIA_01/ack.` y
`[Red] 1 lectura(s) con acuse logico; quedan 0.` en cada ciclo de 30 s.

Mutaciones rechazadas con `rejected` y sin persistir: null con status OK, −127 como valor, `sensor_fault`
incoherente, `captured_at` null sin `unsynced`, campo extra, `firmware_version` sin `v`.
