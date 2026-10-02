# Evidencia de QA y Validación — HU-11: Publicación de Telemetría (QoS 1) con Idempotencia

## 1. Resumen de la Historia de Usuario
- **ID**: HU-11
- **Título**: Publicación de Telemetría (QoS 1) con Idempotencia
- **Responsable**: Diego Soto / Brenda Gamio
- **Objetivo**: Asegurar que la telemetría se envíe con QoS 1 (confirmación PUBACK) y se procese de forma idempotente en el backend mediante clave única `(device_id, boot_id, seq_no)`. Los reintentos conservan la misma identidad, los duplicados idénticos se descartan pacíficamente y cualquier conflicto de contenido para la misma identidad se registra en la tabla de auditoría. Si se agotan los reintentos en línea, el firmware retiene el dato en el buffer LittleFS sin pérdida.

---

## 2. Checklist de Verificación y Resultados

| Tarea | Rol | Criterio Verificado | Estado | Evidencia |
|---|---|---|---|---|
| **Verificar espera de PUBACK** | `[FW]` | En transmisión QoS 1, el nodo espera el acuse del broker (PUBACK). El PUBACK no elimina la lectura del buffer (regla de oro: solo el acuse lógico del backend libera el registro). | **PASS** | `TransmisorMQTT.cpp` & `test_sin_acuse_logico_no_se_borra_nada` |
| **Probar reintento con misma identidad** | `[FW]` | Cada retransmisión conserva estrictamente `device_id`, `boot_id` y `seq_no` originales. | **PASS** | `test_fifo_estricto_entre_segmentos` & `test_hu11_fw_identidad_invariable_y_buffer_offline` |
| **Probar descarte de lectura duplicada** | `[BE]` | La recepción repetida de una telemetría ya persistida con idéntico payload responde `status = "duplicate"` y no genera filas redundantes. | **PASS** | `test_hu11_be_descarte_de_lectura_duplicada` |
| **Probar registro de conflicto de payload** | `[BE]` | Si llega la misma tupla `(device_id, boot_id, seq_no)` con datos discrepantes, el backend preserva el original y registra el conflicto con timestamp en la tabla `conflictos`. | **PASS** | `test_hu11_be_registro_de_conflicto_de_payload` |
| **Verificar paso a buffer al agotar reintentos** | `[FW]` | Si no hay conectividad o se agotan los reintentos, la lectura no se descarta; se almacena y preserva en el buffer offline de LittleFS hasta que el canal se restablezca. | **PASS** | `test_saturacion_preserva_antiguas_y_cuenta_perdidas` (Unity C++) |

---

## 3. Logs de Ejecución

### Backend (Pytest):
```text
backend/tests/test_hu11_idempotencia.py::test_hu11_be_persistencia_inicial PASSED
backend/tests/test_hu11_idempotencia.py::test_hu11_be_descarte_de_lectura_duplicada PASSED
backend/tests/test_hu11_idempotencia.py::test_hu11_be_registro_de_conflicto_de_payload PASSED
backend/tests/test_hu11_idempotencia.py::test_hu11_fw_identidad_invariable_y_buffer_offline PASSED
============================== 4 passed in 0.62s ===============================
```

### Firmware C++ (Unity Native):
```text
iot/test/test_core/main.cpp:461:test_fifo_estricto_entre_segmentos:PASS
iot/test/test_core/main.cpp:462:test_sin_acuse_logico_no_se_borra_nada:PASS
iot/test/test_core/main.cpp:463:test_reanuda_desde_el_ultimo_confirmado:PASS
iot/test/test_core/main.cpp:465:test_saturacion_preserva_antiguas_y_cuenta_perdidas:PASS
-----------------------
77 Tests 0 Failures 0 Ignored 
OK
```
