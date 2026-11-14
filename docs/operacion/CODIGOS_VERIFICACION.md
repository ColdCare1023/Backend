# Códigos de verificación por correo

Códigos de un solo uso de 6 dígitos enviados por correo para tres flujos:

1. **Segundo factor del login con contraseña** (`OTP_LOGIN_ENABLED`).
2. **Recuperación de contraseña** («¿Olvidaste tu contraseña?»).
3. **Activación de cuentas** creadas por un administrador (o por `scripts.asegurar_admins`).

El login con Google no pide código: Google ya verificó la identidad del titular.

## Variables de entorno

Solo nombres; los valores van en el gestor de secretos de la plataforma (Railway), nunca en el repositorio.

| Variable | Por defecto | Uso |
|---|---|---|
| `OTP_LOGIN_ENABLED` | `false` | Activa el segundo factor del login con contraseña. En producción exige el correo habilitado. |
| `CODIGO_TTL_MINUTOS` | `10` | Vigencia de los códigos de login y de recuperación. |
| `CODIGO_ACTIVACION_TTL_HORAS` | `48` | Vigencia del código de activación. |
| `CODIGO_MAX_INTENTOS` | `5` | Intentos fallidos por código; al agotarlos el código se quema. |
| `CODIGO_REENVIO_SEGUNDOS` | `60` | Espera mínima entre dos envíos del código de un mismo login. |
| `CODIGO_MAX_FALLOS_CUENTA` | `20` | Tope de intentos fallidos por cuenta sumando todos los códigos de login (o de recuperación+activación) emitidos en la ventana. Superado, no se emiten ni se aceptan códigos de ese tipo. |
| `CODIGO_VENTANA_FALLOS_HORAS` | `24` | Ventana del tope anterior. |
| `CODIGOS_HMAC_KEY` | (vacía) | Clave HMAC de los códigos guardados. En producción: ≥ 32 bytes, distinta de `JWT_SECRET_KEY` y de `TRAZABILIDAD_HMAC_KEY`; obligatoria si el correo u OTP están habilitados. Fuera de producción, vacía = clave derivada de `JWT_SECRET_KEY`. |
| `EMAIL_ENABLED` | `false` | Habilita el correo (alertas, recuperación, códigos). |
| `EMAIL_TRANSPORTE` | `smtp` | `smtp`, `http_api` (Resend) o `apps_script`. |
| `EMAIL_FROM` | | Remitente (obligatorio para enviar). |
| `EMAIL_FROM_NOMBRE` | `ColdCare` | Nombre visible del remitente (relé de Apps Script). |
| `EMAIL_API_URL` | Resend | `http_api`: endpoint del proveedor. `apps_script`: URL `/exec` del despliegue (HTTPS). |
| `EMAIL_API_KEY` | | `http_api`: API key. `apps_script`: secreto compartido con el script. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_SEGURIDAD`, `SMTP_USER`, `SMTP_PASSWORD` | | Transporte `smtp` (`ssl` o `starttls`). |
| `FRONTEND_URL` | | Origen del frontend (HTTPS en producción). Los correos enlazan a `FRONTEND_URL/recuperar-contrasena`. |
| `PASSWORD_RESET_URL` | | Página del enlace de recuperación heredado (`#token=`). Si queda en su valor de desarrollo, se usa `FRONTEND_URL/restablecer-password`. En producción, con cualquier transporte de correo, la URL efectiva debe ser HTTPS. |

Generar `CODIGOS_HMAC_KEY`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Cambiar `CODIGOS_HMAC_KEY` invalida los códigos pendientes (no las sesiones ni las contraseñas).

## Almacenamiento

Tabla `codigos_verificacion` (migración `0038_codigos_verificacion`):

- `codigo_hash`: HMAC-SHA256 (hex) de `usuario_id:proposito:codigo` con `CODIGOS_HMAC_KEY`. El código en claro solo viaja en el correo.
- `desafio_hash`: SHA-256 del desafío opaco del login (`secrets.token_urlsafe(32)`), único cuando existe.
- `intentos`, `usado_en`, `expira_en`: los intentos fallidos se cuentan en la base con un `UPDATE` atómico; consumir es un `UPDATE` condicional (compare-and-set), así que dos peticiones no pueden usar el mismo código.
- `creado_en` marca el último envío (la espera del reenvío se mide desde ahí); `emitido_en` es la emisión original y no cambia. Un reenvío nunca lleva la caducidad más allá del doble de la vigencia desde `emitido_en`.
- Emitir un código nuevo **anula** (marca `usado_en`) los vivos del mismo grupo: login, o recuperación+activación (un código de recuperación anula el de activación y al revés). Un índice único parcial (`usuario_id, proposito` donde `usado_en IS NULL`) y el bloqueo de la fila del usuario impiden dos códigos vivos aunque lleguen dos solicitudes a la vez; además, cada intento se compara solo con el código vivo más reciente.
- Los códigos anulados, usados o quemados se conservan `CODIGO_VENTANA_FALLOS_HORAS` (se purgan al emitir el siguiente) para que sus intentos sigan contando en el tope por cuenta. Pedir códigos nuevos no da intentos nuevos sin límite, y el tope sobrevive a reinicios y despliegues (los limitadores en memoria no).

### Tope de fallos por cuenta

Pasados `CODIGO_MAX_FALLOS_CUENTA` fallos en la ventana:

- login con código: `POST /api/auth/login` y `/login/reenviar` responden `429` (`Retry-After`), y `/login/verificar` rechaza el código con `401`;
- recuperación: `POST /api/auth/recuperar-password` responde el mismo `202` sin enviar nada, y `restablecer-password` con código responde el `400` genérico.

Se reinicia al pasar la ventana o cuando un administrador restablece la contraseña o reactiva la cuenta (borra el historial de códigos). Cualquier cambio de contraseña olvida los fallos de los códigos de login (quien los acumulaba ya no conoce la contraseña). Emitir un código de activación (alta por administrador o `asegurar_admins`) reinicia el tope de los códigos de contraseña.

### Cambios de contraseña

Cualquier cambio de contraseña (`/api/auth/cambiar-password`, restablecimiento por código o enlace, restablecimiento o reactivación por un administrador) cierra las sesiones y anula los enlaces y **todos** los códigos pendientes: el código de activación de 48 h deja de valer en cuanto la persona elige o cambia su contraseña. Un login con código empezado antes de un cambio de contraseña o de un cierre de sesiones no se puede completar después.

## Flujos

### 1. Login con código (`OTP_LOGIN_ENABLED=true`)

1. `POST /api/auth/login` (form `username`, `password`). Con credenciales válidas **no** devuelve token:

   ```json
   {"access_token": null, "token_type": "bearer", "requiere_codigo": true,
    "desafio": "<opaco>", "email_enmascarado": "a***@farmacia.pe",
    "expira_en_segundos": 600, "reenviar_en_segundos": 60,
    "require_privacy_consent": false, "require_password_change": false}
   ```

   El código se envía antes de responder; si el correo falla, `503`. Si el correo no está configurado, `503` antes de mirar la contraseña (falla cerrado).
2. `POST /api/auth/login/verificar` `{"desafio", "codigo"}` → `200` con el `TokenResponse` de siempre (`access_token`, `require_password_change`, `require_privacy_consent`). Errores: `401` con mensaje genérico (código incorrecto, intentos agotados o verificación caducada); `429` si la cuenta superó su cuota de fallos.
3. `POST /api/auth/login/reenviar` `{"desafio"}` → `200` con la misma forma del paso 1 (mismo desafío, código nuevo). Durante la espera: `429` con `Retry-After`. Reenviar no reinicia los intentos fallidos.

Auditoría: `LOGIN_FALLIDO`, `LOGIN_CODIGO_EMITIDO`, `LOGIN_CODIGO_REENVIADO`, `LOGIN_CODIGO_FALLIDO`, `LOGIN_EXITOSO` con `{"metodo": "password+codigo"}`.

Con `OTP_LOGIN_ENABLED=false` el login es exactamente el anterior (`{"metodo": "password"}`).

### 2. Recuperación por código

1. `POST /api/auth/recuperar-password` `{"email"}` → siempre `202` con el mismo cuerpo, exista o no la cuenta. Antes de responder solo se aplican las cuotas y se audita; buscar la cuenta, emitir el código y enviar el correo ocurre después de responder, así que el tiempo de respuesta tampoco revela si la cuenta existe. Si existe y está activa se envía un correo con el código de 10 minutos y, por compatibilidad, el enlace de un solo uso de 30 minutos. Cuotas: 5 por IP cada 15 min (`429`) y 3 por cuenta cada 15 min (pasada, misma `202` sin enviar nada). Auditoría `RECUPERACION_PASSWORD_SOLICITADA` con el correo enmascarado.
2. `POST /api/auth/restablecer-password`:
   - por código: `{"email", "codigo", "password_nueva"}` (acepta un código de recuperación o de activación);
   - heredado: `{"token", "password_nueva"}`.

   `nueva_password` se acepta como sinónimo de `password_nueva`. Respuesta `204`. La contraseña sigue la política (≥ 10 caracteres, letras y números), deja de ser temporal, se cierran todas las sesiones y se anulan enlaces y códigos pendientes. Error: `400` genérico. Auditoría `PASSWORD_RESTABLECIDA` (`{"metodo": "codigo", "proposito"}`), `PASSWORD_RESTABLECIDA_CON_ENLACE` o `RESTABLECER_CODIGO_FALLIDO`.

### 3. Activación de cuentas creadas por un administrador

`POST /api/usuarios` sigue igual para el administrador. Si hay correo configurado, en la misma transacción se emite un código de activación de 48 h y se envía la bienvenida con el código y el enlace `FRONTEND_URL/recuperar-contrasena?email=<correo>&modo=activar`. El correo nunca lleva una contraseña. La persona elige su contraseña con `POST /api/auth/restablecer-password` (`email` + `codigo`). La bitácora `BIENVENIDA_NOTIFICADA` indica `codigo_activacion`.

### Administradores de arranque

```bash
python -m scripts.asegurar_admins --email ana@farmacia.pe --nombre "Ana Pérez" \
                                  --email luis@farmacia.pe --nombre "Luis Soto"
```

Idempotente: crea la cuenta (administrador, activa, contraseña aleatoria inutilizable que nunca se imprime) o la deja como administradora activa, y envía el código de activación si hay correo. Si la cuenta estaba **desactivada**, al reactivarla se sustituye su contraseña por una inutilizable (la anterior pudo ser la razón de la baja), se cierran sus sesiones y se anulan sus enlaces y códigos pendientes: la persona elige una contraseña nueva con el código de activación. Sin correo (`--sin-correo` o sin transporte) la salida avisa que la cuenta quedó sin contraseña utilizable (un administrador puede asignarle una temporal). Solo imprime correos enmascarados y el estado.

## Relé de Google Apps Script (`EMAIL_TRANSPORTE=apps_script`)

El backend hace `POST EMAIL_API_URL` (HTTPS, timeout 15 s), **una petición por destinatario**, con:

```json
{"secreto": "<EMAIL_API_KEY>", "to": ["a@x.pe"], "subject": "...", "text": "...",
 "html": "...", "nombre_remitente": "ColdCare"}
```

Apps Script contesta el POST con un `302` a `script.googleusercontent.com`. El backend no sigue redirecciones automáticamente: sigue a mano solo un `302`/`303`, con `GET` y sin cuerpo, y solo si va por HTTPS a ese host. Cualquier otra redirección es un fallo (el cuerpo, con el secreto, nunca se reenvía). Solo es éxito un HTTP `200` con JSON `{"ok": true}`.

El script debe **fallar cerrado**: rechazar si la propiedad `SECRETO` no existe o es corta, comparar en tiempo constante, aceptar exactamente un destinatario con forma de correo, acotar asunto y cuerpo, y fijar el nombre del remitente en el propio script (no confiar en `nombre_remitente`). Esqueleto:

```javascript
var NOMBRE_REMITENTE = 'ColdCare';
var MAX_ASUNTO = 200;
var MAX_CUERPO = 100000;
var CORREO = /^[^\s@,;<>"]+@[^\s@,;<>"]+\.[^\s@,;<>"]+$/;

function json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function digest(valor) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, valor, Utilities.Charset.UTF_8);
}

// Compara los SHA-256 de ambos valores byte a byte sin salir antes: el tiempo
// no depende de cuántos caracteres coinciden ni de la longitud del secreto.
function iguales(a, b) {
  var x = digest(a), y = digest(b), dif = 0;
  for (var i = 0; i < x.length; i++) dif |= (x[i] ^ y[i]);
  return dif === 0;
}

function doPost(e) {
  var d;
  try { d = JSON.parse(e.postData.contents); } catch (err) { return json({ok: false}); }
  var esperado = PropertiesService.getScriptProperties().getProperty('SECRETO');
  // Sin secreto configurado (getProperty devuelve null) o con uno débil, nadie pasa.
  if (!esperado || esperado.length < 32) return json({ok: false});
  if (!d || typeof d.secreto !== 'string' || !iguales(d.secreto, esperado)) return json({ok: false});
  if (!Array.isArray(d.to) || d.to.length !== 1 || typeof d.to[0] !== 'string' || !CORREO.test(d.to[0])) {
    return json({ok: false});
  }
  if (typeof d.subject !== 'string' || d.subject.length > MAX_ASUNTO) return json({ok: false});
  if (typeof d.text !== 'string' || d.text.length > MAX_CUERPO) return json({ok: false});
  if (d.html !== undefined && (typeof d.html !== 'string' || d.html.length > MAX_CUERPO)) return json({ok: false});
  GmailApp.sendEmail(d.to[0], d.subject, d.text, {htmlBody: d.html || undefined, name: NOMBRE_REMITENTE});
  return json({ok: true});
}
```

Despliegue: «Aplicación web», ejecutar como el propietario, acceso «Cualquier usuario» (la URL `/exec` es pública: por eso el script valida todo lo anterior). El secreto (≥ 32 caracteres, p. ej. `python -c "import secrets; print(secrets.token_urlsafe(32))"`) se guarda en las propiedades del script como `SECRETO`, no en el código, y es el mismo valor que `EMAIL_API_KEY`. El backend nunca registra el secreto ni el cuerpo del correo. Si el secreto se filtra, cámbielo en ambos lados: con él se puede enviar correo como la cuenta del script a cualquier destinatario.

## Interruptor de emergencia

Si el correo deja de salir y nadie puede entrar:

1. Poner `OTP_LOGIN_ENABLED=false` en las variables del servicio y redeplegar (o reiniciar). El login vuelve a ser solo con contraseña; los códigos pendientes dejan de pedirse.
2. Revisar el transporte (`/health/detalle`, logs `correo_transaccional`) y volver a `true` cuando funcione.

La recuperación por correo responde `503` mientras no haya transporte; el administrador puede asignar una contraseña temporal desde la gestión de usuarios.
