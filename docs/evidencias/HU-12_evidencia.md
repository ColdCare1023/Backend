# Evidencia — HU-12: Recepción continua de telemetría

Entorno: `infra-local/` (PostgreSQL 16, EMQX 5.8.6 con TLS 8883 y CA propia, backend FastAPI en Docker), nodo ESP32 DevKitC real con firmware ColdCare1023/IoT `develop` (v1.2.0) salvo donde se indique, frontend ColdCare1023/Frontend `main`. Fechas: 2–3 de octubre de 2026.

> Reemplaza la evidencia anterior de esta HU, que se apoyaba en pruebas sobre módulos simulados (cliente MQTT sin conexión real, broker y navegador falsos en Python) y no ejercitaba el sistema real.
## Defecto corregido
`src/consumidor_mqtt.py` **no se conectaba a MQTT**: no creaba cliente, `start()` solo marcaba
`is_connected = True` y la reconexión eran métodos `simulate_*`. Se reemplaza por el servicio FastAPI con
ingesta real (`aiomqtt`), persistencia en PostgreSQL, SSE autenticado y API REST, que es la API que consume
ColdCare1023/Frontend. Se corrigieron además dependencias faltantes (`httpx`, `greenlet`), la lectura de
metadatos de la IA en UTF-8 (fallaba en Linux) y la suscripción, que era QoS 0.

## Resultados en vivo
| Tarea | Resultado |
|---|---|
| [BE] Suscripción al iniciar | ✅ 8 s tras arrancar: `farmacias/+/lecturas` y `farmacias/+/eventos` en **QoS 1** |
| [QA] Reconexión tras corte del broker (20 s) | ✅ Backoff 2 → 4 → 8 → 16 s y re-suscripción automática |
| [QA] Varios dispositivos simultáneos | ✅ 5 nodos × 10 lecturas: 50/50 persistidas y acusadas |
| [QA] API y MQTT en paralelo | ✅ 29/29 respuestas 200 de `/api/lecturas` durante la ráfaga (p50 22 ms) |

Script de carga: `infra-local/scripts/hu12_hu19_carga.py` (contrato v1.0).
