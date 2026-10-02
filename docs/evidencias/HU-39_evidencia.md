# Evidencia de QA y Validación — HU-39: Autenticación de Usuarios (Login Seguro)

## 1. Resumen de la Historia de Usuario
- **ID**: HU-39
- **Título**: Autenticación de Usuarios (Login seguro)
- **Responsable**: Diego Soto
- **Objetivo**: Asegurar el acceso a la plataforma únicamente mediante credenciales válidas validadas con algoritmo Argon2id, generando un token JWT con identidad y rol, mitigando ataques de fuerza bruta mediante bloqueo temporal tras 5 intentos fallidos consecutivos, auditando cada intento sin revelar si falló el correo o la contraseña, y bloqueando el acceso a usuarios desactivados o con tokens expirados.

---

## 2. Checklist de Verificación y Resultados

| Tarea | Rol | Criterio Verificado | Estado | Evidencia |
|---|---|---|---|---|
| **Verificar login con credenciales válidas** | `[BE]` | El usuario presenta credenciales correctas; la verificación con Argon2id es exitosa y se emite un JWT firmado con rol, email y tiempo de vida de 15 minutos. | **PASS** | `test_hu39_be_login_con_credenciales_validas` |
| **Probar rechazo con credenciales inválidas** | `[BE]` | Contraseñas incorrectas o correos inexistentes reciben código HTTP 401 con el mensaje opaco "Credenciales inválidas" para mitigar ataques de enumeración; cada intento se registra en el log de auditoría. | **PASS** | `test_hu39_be_rechazo_con_credenciales_invalidas` |
| **Probar bloqueo por intentos fallidos** | `[BE]` | Al llegar a 5 intentos fallidos consecutivos sobre una cuenta, el endpoint bloquea temporalmente las solicitudes retornando HTTP 429 Too Many Requests con tiempo de espera. | **PASS** | `test_hu39_be_bloqueo_por_intentos_fallidos` |
| **Verificar expiración de sesión** | `[FE]` | Solicitudes que presenten un token JWT con `exp` vencido son rechazadas con HTTP 401 ("Token expirado"), obligando a la SPA a invalidar la sesión en memoria y redirigir al login. | **PASS** | `test_hu39_fe_verificar_expiracion_de_sesion` |
| **Registrar evidencia** | `[QA]` | Ejecución de suite de pruebas unitarias y de integración con TestClient. | **PASS** | Reporte registrado. |

---

## 3. Logs de Ejecución

```text
backend/tests/test_hu39_auth_segura.py::test_hu39_be_login_con_credenciales_validas PASSED
backend/tests/test_hu39_auth_segura.py::test_hu39_be_rechazo_con_credenciales_invalidas PASSED
backend/tests/test_hu39_auth_segura.py::test_hu39_be_bloqueo_por_intentos_fallidos PASSED
backend/tests/test_hu39_auth_segura.py::test_hu39_be_usuario_desactivado_no_autentica PASSED
backend/tests/test_hu39_auth_segura.py::test_hu39_fe_verificar_expiracion_de_sesion PASSED
========================= 5 passed in 1.13s =========================
```

### Log de auditoría generado:
```json
[
  {"timestamp": "2026-10-02T17:09:12.100Z", "email": "diego.soto@coldcare.pe", "ip": "testclient", "resultado": "exitoso", "motivo": null},
  {"timestamp": "2026-10-02T17:09:12.180Z", "email": "tecnico@coldcare.pe", "ip": "testclient", "resultado": "rechazado", "motivo": "Contraseña errónea (intento 1)"},
  {"timestamp": "2026-10-02T17:09:12.210Z", "email": "no_existe@coldcare.pe", "ip": "testclient", "resultado": "rechazado", "motivo": "Usuario no existe o desactivado"},
  {"timestamp": "2026-10-02T17:09:12.450Z", "email": "tecnico@coldcare.pe", "ip": "testclient", "resultado": "bloqueo_activado", "motivo": "Bloqueo activado por 900 s"}
]
```
