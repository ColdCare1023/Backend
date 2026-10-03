# Evidencia — HU-40: Sesión JWT en memoria

Entorno: `infra-local/` (PostgreSQL 16, EMQX 5.8.6 con TLS 8883 y CA propia, backend FastAPI en Docker), nodo ESP32 DevKitC real con firmware ColdCare1023/IoT `develop` (v1.2.0) salvo donde se indique, frontend ColdCare1023/Frontend `main`. Fechas: 2–3 de octubre de 2026.

> Reemplaza la evidencia anterior de esta HU, que se apoyaba en pruebas sobre módulos simulados (cliente MQTT sin conexión real, broker y navegador falsos en Python) y no ejercitaba el sistema real.
## Resultados (navegador real)
| Tarea | Resultado |
|---|---|
| [FE] JWT fuera de localStorage | ✅ Ningún valor `eyJ…` en `localStorage` |
| [FE] JWT fuera de sessionStorage | ✅ Solo la marca `cf_sesion_previa = '1'` (no es un token) |
| [FE] Reautenticación tras cerrar/recargar | ✅ Recargar `/dashboard` redirige a `/login` |
| [FE] Logout elimina el token | ✅ `POST /api/auth/logout` 204; `/dashboard` → `/login` |

Revocación en el backend: el mismo token después del logout → **401** en `/api/lecturas` y en
`/api/auth/sse-ticket`. Limitación conocida: la lista de revocación vive en memoria; si el backend se
reinicia, un token revocado vuelve a valer hasta su expiración (60 min).
