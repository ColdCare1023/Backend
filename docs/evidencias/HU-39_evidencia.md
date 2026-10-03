# Evidencia — HU-39: Inicio de sesión seguro

Entorno: `infra-local/` (PostgreSQL 16, EMQX 5.8.6 con TLS 8883 y CA propia, backend FastAPI en Docker), nodo ESP32 DevKitC real con firmware ColdCare1023/IoT `develop` (v1.2.0) salvo donde se indique, frontend ColdCare1023/Frontend `main`. Fechas: 2–3 de octubre de 2026.

> Reemplaza la evidencia anterior de esta HU, que se apoyaba en pruebas sobre módulos simulados (cliente MQTT sin conexión real, broker y navegador falsos en Python) y no ejercitaba el sistema real.
## Resultados API (backend real)
```
== Esc. 1: credenciales válidas
200 rol: tecnico · expira en 60 min
== Esc. 2: rechazo genérico
correo inexistente : (401, 'Email o contraseña incorrectos')
contraseña errónea : (401, 'Email o contraseña incorrectos')
== Esc. 3: bloqueo (5 fallos en 300 s por IP)
intento fallido #6: (429, 'Demasiados intentos fallidos…', Retry-After 290)
credenciales VÁLIDAS durante el bloqueo: 429
audit_logs: LOGIN_EXITOSO 1 · LOGIN_FALLIDO 5
```

## Expiración de sesión (frontend)
Con `JWT_EXPIRE_MIN=1`, al expirar el token el frontend cierra la sesión y vuelve al login. El aviso
"Tu sesión expiró. Inicia sesión nuevamente." **no se mostraba** (el motivo se guardaba pero nadie lo leía);
corregido en ColdCare1023/Frontend `LoginPage.tsx` y verificado en el navegador.
