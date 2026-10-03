# Evidencia — HU-09: Canal MQTT cifrado con validación del broker

Entorno: `infra-local/` (PostgreSQL 16, EMQX 5.8.6 con TLS 8883 y CA propia, backend FastAPI en Docker), nodo ESP32 DevKitC real con firmware ColdCare1023/IoT `develop` (v1.2.0) salvo donde se indique, frontend ColdCare1023/Frontend `main`. Fechas: 2–3 de octubre de 2026.

## Cambios
- `infra-local/`: EMQX 5.8.6 con TLS 1.2/1.3 en 8883 y CA propia (`scripts/init.sh` genera CA,
  certificado del broker con la IP como `DNS:` e `IP:` —mbedTLS 2.x compara contra DNS— y certificados para
  los casos negativos).
- Backend: `MQTT_CA_FILE` permite confiar en la CA privada **sin relajar** la verificación de certificado
  y hostname (`ssl.create_default_context(cafile=...)`).

## Resultados
| Tarea | Resultado |
|---|---|
| [FW] Cargar CA en el nodo | ✅ `data/certs/root_ca.pem` vía `uploadfs` (`flash_coldcare.sh`) |
| [FW] Conexión TLS con EMQX real | ✅ `[MQTT] Conectado (TLS, QoS 1)` — ESP32_FARMACIA_01 |
| [QA] Rechazo con certificado inválido | ✅ CA ajena → handshake rechazado |
| [QA] Rechazo con host distinto | ✅ `Hostname mismatch, certificate is not valid for 'otro-host.invalido'` |
| [FW] Timeout de handshake y reconexión | ✅ Broker caído: reintentos con backoff, reconexión automática al volver |

Prueba automatizada: `infra-local/scripts/smoke_test.py`.
