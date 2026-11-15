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

## Tope de flujos SSE por cuenta (esc. 3, BE-019)

Cada flujo SSE retiene una cola y una conexión durante horas, así que una
cuenta puede tener abiertos a la vez como máximo `SSE_MAX_STREAMS_POR_USUARIO`
flujos (pestañas o dispositivos con el dashboard; **3 por defecto**,
configurable de 1 a 100 en las variables del servicio). El escenario 3 («más
de una pestaña o dispositivo») se cumple hasta ese número.

Pasado el tope:

| Petición | Respuesta |
|---|---|
| `POST /api/auth/sse-ticket` (fetch del frontend) | `429`, `{"detail": "demasiados_flujos"}`, `Retry-After: 15` |
| `GET /api/sse/lecturas?ticket=…` (EventSource) | el mismo `429` (solo si dos pestañas pidieron ticket a la vez) |

El código va en el ticket porque `EventSource` no expone ni el estado ni el
cuerpo de un error: antes el `429` solo llegaba al stream y la pestaña quedaba
en «Reconectando…» sin explicación. El frontend debe mostrar «Demasiadas
pestañas en tiempo real» al recibir `demasiados_flujos`. El cupo se libera al
cerrar una pestaña (el backend lo detecta en la siguiente vuelta del flujo,
como mucho 15 s).

Pruebas: `tests/integration/test_hu32_tope_flujos_sse.py`,
`tests/integration/test_be019_consumo_recursos.py`.
