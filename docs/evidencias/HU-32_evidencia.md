# Evidencia — HU-32: Actualización en vivo por SSE

Entorno: `infra-local/` (PostgreSQL 16, EMQX 5.8.6 con TLS 8883 y CA propia, backend FastAPI en Docker), nodo ESP32 DevKitC real con firmware ColdCare1023/IoT `develop` (v1.2.0) salvo donde se indique, frontend ColdCare1023/Frontend `main`. Fechas: 2–3 de octubre de 2026.

> Reemplaza la evidencia anterior de esta HU, que se apoyaba en pruebas sobre módulos simulados (cliente MQTT sin conexión real, broker y navegador falsos en Python) y no ejercitaba el sistema real.
## Resultados (frontend real + ESP32)
| Tarea | Resultado |
|---|---|
| [FE] Actualización sin recargar | ✅ "Última actualización" 10:42:39 → 10:43:39 en la misma página, sin nuevas peticiones a `/api/lecturas` |
| [FE] Rotulado "En vivo" | ✅ Insignia "En vivo" (antes decía "Actualización automática activa") |
| [FE] Reconexión SSE | ✅ Backend detenido: "Reconectando al flujo de lecturas…" a los ~7 s; recuperado ~8 s después de volver |

La reconexión era a intervalo fijo de 5 s (la evidencia anterior afirmaba backoff exponencial); ahora es
1 → 2 → 4 … 30 s y vuelve a 1 s al reconectar (ColdCare1023/Frontend `sseClient.ts`, con prueba).
Nota: con el proxy de desarrollo de Vite la caída del backend no llega al `EventSource`; la prueba se hizo
con el frontend apuntando directo al backend, como en producción.
