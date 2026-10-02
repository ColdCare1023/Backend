# Evidencia de QA y Validación — HU-40: Gestión de Sesión JWT en Memoria

## 1. Resumen de la Historia de Usuario
- **ID**: HU-40
- **Título**: Gestión de sesión JWT en memoria
- **Responsable**: Diego Soto
- **Objetivo**: Asegurar que la SPA conserve el token JWT estrictamente en memoria volátil de JavaScript, evitando su persistencia en `localStorage` o `sessionStorage`, reduciendo el riesgo de robo por scripts maliciosos. Al cerrar el contexto (pestaña/navegador) se requiere reautenticación, y al pulsar logout se destruye el token en memoria y se revoca en el servidor.

---

## 2. Checklist de Verificación y Resultados

| Tarea | Rol | Criterio Verificado | Estado | Evidencia |
|---|---|---|---|---|
| **Verificar JWT fuera de localStorage** | `[FE]` | El token no se escribe en `window.localStorage` tras iniciar sesión; el almacenamiento permanece limpio de credenciales. | **PASS** | `test_hu40_fe_jwt_fuera_de_local_storage` |
| **Verificar JWT fuera de sessionStorage** | `[FE]` | En entorno de producción no demo, `sessionStorage` no almacena el JWT. | **PASS** | `test_hu40_fe_jwt_fuera_de_session_storage` |
| **Probar reautenticación tras cerrar** | `[FE]` | Al recargar o reabrir la aplicación en una pestaña nueva, la ausencia de almacenamiento persistente obliga al usuario a identificarse nuevamente en rutas protegidas. | **PASS** | `test_hu40_fe_reautenticacion_tras_cerrar_contexto` |
| **Probar que el logout elimina el token** | `[FE]` | El cierre de sesión limpia el token en memoria (`setAccessToken(null)`) y llama al backend para invalidar el `jti`, denegando llamadas posteriores con el mismo token. | **PASS** | `test_hu40_fe_logout_elimina_token_y_revoca` |
| **Registrar evidencia** | `[QA]` | Ejecución de casos de prueba del plan de seguridad de sesión. | **PASS** | Reporte registrado. |

---

## 3. Logs de Ejecución

```text
backend/tests/test_hu40_jwt_en_memoria.py::test_hu40_fe_jwt_fuera_de_local_storage PASSED
backend/tests/test_hu40_jwt_en_memoria.py::test_hu40_fe_jwt_fuera_de_session_storage PASSED
backend/tests/test_hu40_jwt_en_memoria.py::test_hu40_fe_reautenticacion_tras_cerrar_contexto PASSED
backend/tests/test_hu40_jwt_en_memoria.py::test_hu40_fe_logout_elimina_token_y_revoca PASSED
========================= 4 passed in 1.47s =========================
```
