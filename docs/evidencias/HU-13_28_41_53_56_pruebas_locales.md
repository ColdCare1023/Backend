# Evidencia — HU-13, HU-28, HU-41, HU-53 y HU-56: pruebas en entorno local con nodo real

## Entorno

- **Fecha:** 8 de octubre de 2026, de 20:30 a 21:20 (hora de Perú).
- **Pila local:** `infra-local/`:
  - PostgreSQL 16 en Docker;
  - EMQX 5.8.6 con TLS 8883, CA propia y ACL por dispositivo;
  - backend construido con el `Dockerfile` de la raíz (HU-56).
- **Código:** rama `feat/hu-56-53-13-28-41` en Backend, Frontend e IoT.
- **Nodo:** ESP32 DevKitC (`ESP32_FARMACIA_01`) en COM6. Se reflasheó con el firmware ColdCare v1.2.0 de la rama: LittleFS con la CA y la app. Conservó su configuración NVS (boot 24 → 27 durante las pruebas). El sensor está a temperatura ambiente (~26 °C), por lo que **todas sus lecturas son excursión crítica**.
- **Correo:** sumidero HTTP local que imita la API de Resend (`EMAIL_TRANSPORTE=http_api`). El envío a un proveedor real queda para el despliegue.
- **Monitor:** script que registra con hora los mensajes MQTT de `farmacias/+/eventos` y los eventos SSE del backend → [`logs/2026-10-08_monitor_mqtt_sse.log`](logs/2026-10-08_monitor_mqtt_sse.log).
- **Respaldo previo:** `infra-local/evidencia/respaldo_pre_hu_2026-10-08.dump`, base en `0016` antes de migrar.

## Defectos encontrados y corregidos durante la sesión

| # | Defecto | Cómo apareció | Corrección |
|---|---|---|---|
| 1 | **Registrar una acción correctiva devolvía 500 en PostgreSQL.** El `recurso` auditado (`alertas/<uuid>/acciones-correctivas/<uuid>`) mide 102 caracteres y `audit_logs.recurso` es `VARCHAR(100)`. SQLite, que es lo que usan las pruebas, no valida longitudes. Es un defecto **previo** a esta rama: afectaba a HU-27 en cualquier despliegue real. | R1, primer intento. | Recurso acortado a `alertas/<uuid>/acciones/<uuid>`, más la prueba `test_los_recursos_auditados_caben_en_la_columna`. |
| 2 | **HU-13 criterio 5 fallaba:** con el backend caído, el LWT se perdía y el nodo apagado seguía `online`. El cliente MQTT usaba sesión limpia (valor por defecto de aiomqtt), así que el broker no guardaba nada mientras el backend estaba desconectado. Afecta también a lecturas QoS 1 publicadas en ese intervalo (HU-12). | C5, primer intento. | `clean_session=False` en `build_client`, más la prueba `test_el_backend_usa_sesion_mqtt_persistente`. EMQX confirma `clean_start=false` y `session_expiry_interval=7200`. |
| 3 | El seed de desarrollo no tenía usuario auditor (HU-41 tiene 4 roles). | Matriz HU-41. | Usuario `Auditor Demo` agregado a `scripts/seed_dev.py`. |

Suite del backend después de las correcciones: **656 pasan**. Las 4 que fallan son las mismas previas, por fechas fijas en `test_hu05_hu07_payload_y_ack.py`.

## HU-56 · Despliegue

| Verificación | Resultado |
|---|---|
| Imagen desde el `Dockerfile` raíz | ✅ Construye. `alembic upgrade head` corre antes de uvicorn. |
| Migraciones nuevas en PostgreSQL | ✅ `0016 → 0017 → 0018 → 0019` aplicadas sin error. |
| `GET /health/detalle` (administrador) | ✅ `db: ok`, `alembic_revision: 0019_hu28_acciones_append_only`, `mqtt.conectado: true`, `notificaciones.worker_activo: true`. |
| Railway real | ⏳ Pendiente (fase B). |

## HU-13 · Conectividad (nodo real)

| Caso | Acción | Resultado |
|---|---|---|
| C1 | Nodo recién flasheado | ✅ `online` con `boot_id` 24 y `origen: online`. Auditado `DISPOSITIVO_ONLINE`. |
| C2 | Corte de alimentación (20:56:29) | ✅ LWT a las **20:57:29 (~60 s, criterio ≤120 s)**, **con `boot_id: 24`** (firmware nuevo). En el mismo segundo, el SSE `conectividad` pasa a `offline`, `origen: lwt`. Auditado `DISPOSITIVO_OFFLINE`. |
| C3 | Reconexión | ✅ `online` con **`boot_id` 25** en menos de 20 s. El nodo vació el buffer (5 lecturas del arranque 25 acusadas). |
| Criterio 3 | LWT inyectado con `boot_id` 24 (obsoleto) por el broker real, con la credencial del nodo | ✅ Ignorado: estado sigue `online` y queda auditado `LWT_OBSOLETO_IGNORADO {"boot_id_lwt": 24, "boot_id_vigente": 25}`. Sin evento SSE. |
| C4 / criterio 4 | LWT tardío del **mismo** arranque (25) | ✅ Pasa a `offline` y, **17 s después**, la siguiente lectura real lo devuelve a `online` (`origen: telemetria`). |
| C5 | Backend apagado → corte del nodo → backend encendido | ❌ en el primer intento (defecto 2). ✅ tras la corrección: LWT a las 21:12:19 con el backend caído; al encenderlo (21:12:21) lo recibe de la sesión persistente y marca `offline` (boot 26). |
| Frontend | Dashboard con el nodo apagado | ✅ Badge «Sin conexión desde las 09:12:27 p. m.» ([captura](img/HU-13_dashboard_sin_conexion.jpg)). |
| Frontend en vivo | LWT con la página abierta, sin recargar | ✅ El badge cambió **0,3 s** después del LWT (por SSE; el sondeo es cada 15 s) y volvió a «En línea» con la siguiente lectura. |

## HU-53 · Correo de excursión crítica

| Caso | Resultado |
|---|---|
| Recuperación al arrancar | ✅ Al arrancar, el despachador encontró el episodio crítico del nodo abierto desde el 3 de octubre y nunca avisado (la función no existía), y envió **un** correo. Al reiniciar el backend de nuevo **no** lo repitió, porque `notificada_en` es persistente. |
| N1 (FARM-02-TEST, 9,5 °C) | ✅ Correo **~1,3 s** después de la captura. Incluye dispositivo, temperatura, hora de Perú y UTC, y enlace a la alerta. |
| N2 (sostenida, 3 lecturas más; el nodo real también, cada 30 s) | ✅ Un solo correo por episodio. |
| N4 (lectura capturada 20 min antes) | ✅ Asunto `[DIFERIDA]` y cuerpo con «Recibida por el sistema». |
| Reintentos (proveedor responde 503 dos veces) | ✅ 503 (01:51:42), 503 (+30 s), 200 (+2 min). Auditados `NOTIFICACION_FALLIDA` intento 1 y 2 con `HTTP 503`, y `NOTIFICACION_ENVIADA` intento 3. `notificacion_intentos = 2`. |
| Destinatario en auditoría | ✅ Enmascarado (`q***@coldcare.test`). |

Correos recibidos: [`logs/2026-10-08_correos_sumidero.jsonl`](logs/2026-10-08_correos_sumidero.jsonl).

## HU-28 · Rectificación (R1, alerta real del nodo)

| Paso | Resultado |
|---|---|
| Reconocer (farmacéutico) | ✅ 200 |
| Acción correctiva (técnico) | ✅ 201, tras corregir el defecto 1 |
| Rectificar sin motivo | ✅ 422 |
| Rectificar (técnico autor, con motivo) | ✅ 201 |
| Rectificar de nuevo la original (farmacéutico) | ✅ 409: ya no es la versión vigente |
| Rectificar como auditor | ✅ 403 |
| Vínculo por hash (JSONB en PostgreSQL) | ✅ `hash_registro_corregido` = `hash_actual` del eslabón original (chain_seq 98), en la cadena `ESP32_FARMACIA_01` |
| `UPDATE` y `DELETE` directos en `corrective_actions` | ✅ Rechazados por el trigger: `corrective_actions es append-only (HU-28): UPDATE no permitido` (lo mismo para DELETE) |
| Verificación de la cadena del nodo | ✅ `integra: true`, 105 registros |
| UI | ✅ La cronología muestra nombres, «Rectificada», «Rectifica la acción del…» y el motivo. Solo la versión vigente ofrece «Rectificar» ([captura](img/HU-28_cronologia_rectificacion.jpg)). |

## HU-41 · RBAC (contra el backend real)

| Verificación | Resultado |
|---|---|
| Farmacéutico `GET /api/usuarios` · técnico reconoce alerta · auditor crea lectura | ✅ 403 · 403 · 403 |
| Auditor `GET /api/auditoria` · sin token | ✅ 200 · 401 |
| El administrador cambia su propio rol · se desactiva a sí mismo | ✅ 409 · 409. Auditado `RBAC_OPERACION_RECHAZADA` con `motivo: auto_modificacion` |
| Usuario temporal: alta | ✅ 201. Auditoría con el correo enmascarado (`q***@coldcare.example.org`) |
| Cambio de rol técnico → auditor con el **mismo** token | ✅ La siguiente petición ya aplica el nuevo rol (403 → 200) |
| Desactivación | ✅ El token previo da 401. El listado muestra «Usuario desactivado» / «—» |

## Pendiente

- **Fase B:** despliegue en Railway y repetición de C2, C3, N1 y R1 contra EMQX Cloud y un proveedor de correo real.
- **N1 y N3 con el sensor físico:** requieren llevar la sonda al rango de 2 a 8 °C (refrigerador o hielo) para cerrar el episodio. Hoy se cubrieron con dispositivos simulados por el broker real.
