# Evidencia — HU-19: Emisión de eventos SSE

Entorno: `infra-local/` (PostgreSQL 16, EMQX 5.8.6 con TLS 8883 y CA propia, backend FastAPI en Docker), nodo ESP32 DevKitC real con firmware ColdCare1023/IoT `develop` (v1.2.0) salvo donde se indique, frontend ColdCare1023/Frontend `main`. Fechas: 2–3 de octubre de 2026.

> Reemplaza la evidencia anterior de esta HU, que se apoyaba en pruebas sobre módulos simulados (cliente MQTT sin conexión real, broker y navegador falsos en Python) y no ejercitaba el sistema real.
## Resultados
| Tarea | Resultado |
|---|---|
| [BE] Emisión SSE con lectura real | ✅ Lecturas del ESP32 llegan al dashboard por `/api/sse/lecturas` sin recargar |
| [QA] Latencia recepción-emisión | ✅ 50 lecturas, 5 nodos: **p50 249 ms, p95 416 ms, máx 480 ms** (publicación MQTT → evento SSE en el cliente, incluye TLS, broker, persistencia y clasificación IA) |

Medido con `infra-local/scripts/hu12_hu19_carga.py` (cliente SSE real con ticket de un solo uso, contrato
v1.0). La cifra de la evidencia anterior (0,15 ms) medía una cola en memoria, no el camino real.
