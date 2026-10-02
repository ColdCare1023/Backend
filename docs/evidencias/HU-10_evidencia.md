# Evidencia de QA y Validación — HU-10: Autenticación de Dispositivo (device_id + token único + ACL)

## 1. Resumen de la Historia de Usuario
- **ID**: HU-10
- **Título**: Autenticación de Dispositivo (device_id + token único + ACL)
- **Responsable**: Diego Soto / Brenda Gamio
- **Objetivo**: Asegurar que cada nodo ESP32 se autentique ante el broker MQTT con su propio `device_id` como usuario y un token único, aplicando ACL estricta por tópico (solo escribir a sus canales y leer sus propios acuses), bloqueando tokens inválidos y accesos cruzados.

---

## 2. Checklist de Verificación y Resultados

| Tarea | Rol | Criterio Verificado | Estado | Evidencia |
|---|---|---|---|---|
| **Verificar conexión con credencial propia** | `[FW]` | El nodo se autentica con `username = device_id` y token propio; el broker retorna CONNACK `rc=0 (Connection Accepted)`. | **PASS** | `test_hu10_fw_conexion_con_credencial_propia` & `test_una_credencial_real_vale` |
| **Probar rechazo con token inválido** | `[QA]` | Conexiones con contraseña incorrecta o expirada reciben `rc=5 (Not authorized)` y se deniega cualquier publicación. | **PASS** | `test_hu10_qa_rechazo_con_token_invalido` |
| **Probar denegación en tópico ajeno** | `[QA]` | Un dispositivo autenticado no puede publicar ni suscribirse a canales de otros nodos (`farmacias/OTRO/...`); la regla ACL `pattern write farmacias/%u/lecturas` deniega la operación. | **PASS** | `test_hu10_qa_denegacion_en_topico_ajeno` |
| **Capturar logs de EMQX como evidencia** | `[QA]` | Registro y captura estructurada de eventos de autenticación y denegación ACL del broker. | **PASS** | `test_hu10_qa_capturar_logs_emqx_evidencia` |

---

## 3. Logs de Auditoría de EMQX / Broker Capturados

```text
[AUTH] Connection accepted for client 'ESP32_FARMACIA_01' (rc=0: Connection Accepted)
[ACL]  ALLOW ESP32_FARMACIA_01 write on farmacias/ESP32_FARMACIA_01/lecturas
[ACL]  ALLOW ESP32_FARMACIA_01 read on farmacias/ESP32_FARMACIA_01/ack
[AUTH] Connection refused for user 'ESP32_FARMACIA_01': invalid token (rc=5: Not authorized)
[ACL]  DENIED ESP32_FARMACIA_01 write on unauthorized topic 'farmacias/ESP32_FARMACIA_02/lecturas'
[ACL]  DENIED ESP32_FARMACIA_01 read on unauthorized topic 'farmacias/ESP32_FARMACIA_02/ack'
```
