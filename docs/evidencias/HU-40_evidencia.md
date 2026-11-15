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
`/api/auth/sse-ticket`.

## Actualización (14/11/2026)

- **La revocación es persistente.** La limitación anterior («la lista de revocación vive en memoria; si el
  backend se reinicia, un token revocado vuelve a valer») ya no aplica: desde BE-009 los `jti` revocados
  (logout, cambio de rol o de contraseña, desactivación) y los tickets SSE consumidos se guardan en la tabla
  `jti_invalidados` de PostgreSQL. Un token cerrado sigue rechazado después de un reinicio o de un
  redespliegue. Pruebas: `tests/integration/test_be009_revocacion_persistente.py`
  (`test_token_cerrado_no_revive_en_otra_instancia`, `test_cambio_de_rol_invalida_las_sesiones_abiertas`,
  `test_un_ticket_solo_se_consume_una_vez`).
- **Alcance del escenario 4.** La inspección del almacenamiento del navegador y de las solicitudes
  posteriores al logout verifica la **estrategia de sesión** (JWT solo en memoria, nunca en `localStorage`
  ni `sessionStorage`, y no reutilizable tras cerrar sesión). No demuestra inmunidad frente a XSS: un script
  inyectado en la página podría leer la memoria de la aplicación mientras la sesión está abierta. La defensa
  frente a XSS es otra capa (la CSP `script-src 'self'` del frontend), tal como pide
  el criterio («se reporta como verificación de la estrategia de sesión, no como inmunidad frente a XSS»).
