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

## Límites de tamaño

| Tamaño del mensaje | Tratamiento |
|---|---|
| ≤ 768 B (contrato v1.0) | Se valida con el esquema |
| > 768 B y ≤ 5 KiB, con identidad recuperable | Se rechaza con acuse `rejected` (`excede_768_bytes`), esc. 3 |
| > 5 KiB (`MAX_BYTES_PAYLOAD_MQTT`) en `farmacias/<id>/lecturas` | Se descarta **sin acuse** y antes de deserializar; queda auditado como `LECTURA_RECHAZADA_TAMANO` con los bytes recibidos |
| > 5 KiB en `farmacias/<id>/eventos` | Se descarta sin acuse ni auditoría (solo log) |

Por encima de 5 KiB el backend no convierte el mensaje en objetos de Python,
así que no puede extraer una identidad confiable y, como pide el esc. 3, no
inventa un acuse. El firmware nunca publica algo así (no publica payloads de
más de 768 B); un mensaje de ese tamaño no viene del nodo legítimo.

«Mismo payload» (HU-11) significa misma identidad y mismos valores, no los
mismos bytes: ver `docs/evidencias/HU-11_evidencia.md`.

