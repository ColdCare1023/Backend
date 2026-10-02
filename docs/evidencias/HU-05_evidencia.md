# Evidencia de QA y Validación — HU-05: Estructuración Versionada de Telemetría

## 1. Resumen de la Historia de Usuario
- **ID**: HU-05
- **Título**: Estructuración versionada de telemetría (contrato v1.0)
- **Responsable**: Diego Soto / Brenda Gamio
- **Objetivo**: Asegurar que cada lectura se estructure en un payload interoperable, identificable y versionado antes de su transmisión, representando fallas con `null` + `SENSOR_FAULT`, respetando el límite de 768 B y añadiendo `received_at` en el backend.

---

## 2. Checklist de Verificación y Resultados

| Tarea | Rol | Criterio Verificado | Estado | Evidencia |
|---|---|---|---|---|
| **Verificar campos del payload real** | `[FW]` | El payload serializado contiene todos los campos mandatorios (`schema_version`, `device_id`, `boot_id`, `seq_no`, `captured_at`, `time_quality`, `uptime_s`, `temp_ds18b20`, `ds18b20_status`, `temp_sht31`, `humedad_sht31`, `door_open`, `mc38_status`, `sensor_fault`, `firmware_version`). | **PASS** | `test_payload_ejemplo_del_contrato` (77/77 Unity tests OK) |
| **Probar sensor caído como null + SENSOR_FAULT** | `[FW]` | Si DS18B20 o SHT31 falla, se serializa como `null` explícito y `status = SENSOR_FAULT`, nunca un código numérico (-127, 85, etc.). | **PASS** | `test_payload_falla_ds18b20_es_null_con_sensor_fault` & `test_payload_falla_sht31_no_invalida_ds18b20` |
| **Probar rechazo por tamaño excedido** | `[FW]` | Si el payload supera los 768 B configurados como límite físico, no se publica. | **PASS** | `test_payload_peor_caso_cabe_y_el_exceso_no_se_publica` |
| **Verificar received_at en recepción** | `[BE]` | El timestamp `received_at` es generado en el backend en UTC (ISO8601) al ingerir la telemetría, y se rechaza si el nodo intenta enviarlo. | **PASS** | `backend/tests/test_hu05_payload_contrato.py::test_hu05_received_at_en_recepcion` |
| **Registrar evidencia de escenarios** | `[QA]` | Ejecución completa de suites de pruebas nativas y Pydantic en backend y firmware sin fallos. | **PASS** | Documento generado y registrado. |

---

## 3. Logs de Ejecución

### Firmware C++ (Unity Native Test):
```text
iot/test/test_core/main.cpp:315:test_payload_ejemplo_del_contrato:PASS
iot/test/test_core/main.cpp:316:test_payload_falla_ds18b20_es_null_con_sensor_fault:PASS
iot/test/test_core/main.cpp:317:test_payload_falla_sht31_no_invalida_ds18b20:PASS
iot/test/test_core/main.cpp:321:test_payload_peor_caso_cabe_y_el_exceso_no_se_publica:PASS
-----------------------
77 Tests 0 Failures 0 Ignored 
OK
```

### Backend Python (Pytest):
```text
backend/tests/test_hu05_payload_contrato.py::test_hu05_campos_del_payload_real PASSED
backend/tests/test_hu05_payload_contrato.py::test_hu05_sensor_caido_null_y_sensor_fault PASSED
backend/tests/test_hu05_payload_contrato.py::test_hu05_rechazo_por_tamano_excedido PASSED
backend/tests/test_hu05_payload_contrato.py::test_hu05_received_at_en_recepcion PASSED
============================== 4 passed in 0.53s ===============================
```
