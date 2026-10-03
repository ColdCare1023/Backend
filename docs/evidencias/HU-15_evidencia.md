# Evidencia — HU-15: Validación de telemetría entrante

Entorno: `infra-local/` (PostgreSQL 16, EMQX 5.8.6 con TLS 8883 y CA propia, backend FastAPI en Docker), nodo ESP32 DevKitC real con firmware ColdCare1023/IoT `develop` (v1.2.0) salvo donde se indique, frontend ColdCare1023/Frontend `main`. Fechas: 2–3 de octubre de 2026.

> Reemplaza la evidencia anterior de esta HU, que se apoyaba en pruebas sobre módulos simulados (cliente MQTT sin conexión real, broker y navegador falsos en Python) y no ejercitaba el sistema real.
## Defecto corregido
Un payload que no cumplía el esquema terminaba en "Error no controlado" (traza completa) **sin acuse**: el nodo
reintentaba ese registro para siempre y su cola FIFO no avanzaba. Ahora se rechaza de forma controlada,
se audita (`LECTURA_RECHAZADA_ESQUEMA`) y, si trae identidad, recibe acuse `rejected` (el nodo lo pasa a
cuarentena y sigue).

## Resultados en vivo (`python scripts/publicar_caso.py ...`)
| Caso | Acuse |
|---|---|
| null + SENSOR_FAULT | `committed` (aceptado) |
| JSON malformado | sin acuse (sin identidad), auditado |
| Campo faltante | `rejected` |
| `schema_version` no soportado | `rejected` |
| Humedad 120 % / −5 % | `rejected` |
| Lectura válida enviada después | `committed`: un rechazo no detiene el resto |

Pruebas: `test_mutaciones_invalidas_se_rechazan_con_identidad` (6 casos con el contrato v1.0),
`test_payload_fuera_de_contrato_se_acusa_y_no_se_persiste`, `test_json_malformado_se_descarta_sin_excepcion`.
