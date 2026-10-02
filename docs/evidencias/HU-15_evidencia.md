# Evidencia de QA y Validación — HU-15: Validación de Telemetría Entrante

## 1. Resumen de la Historia de Usuario
- **ID**: HU-15
- **Título**: Validación de telemetría entrante
- **Responsable**: Diego Soto
- **Objetivo**: Garantizar que el backend acepte únicamente telemetría con estructura y tipos válidos conforme al esquema Pydantic v1.0, diferenciando de forma inequívoca una falla de sensor (`null` + `SENSOR_FAULT`) de un mensaje malformado o con schema inválido. Ante mensajes rechazados, se emite un acuse lógico `rejected` si la identidad `(device_id, boot_id, seq_no)` es recuperable, sin interrumpir el flujo ni la atención de las demás lecturas.

---

## 2. Checklist de Verificación y Resultados

| Tarea | Rol | Criterio Verificado | Estado | Evidencia |
|---|---|---|---|---|
| **Probar aceptación con null + SENSOR_FAULT** | `[BE]` | Telemetría con sensor desconectado o con error de CRC se valida correctamente como `null` acompañado de su estado `SENSOR_FAULT`. | **PASS** | `test_hu15_be_aceptacion_null_mas_sensor_fault` |
| **Probar rechazo de JSON malformado** | `[BE]` | Payloads con sintaxis rota o datos no deserializables son rechazados y descartados de forma segura sin generar acuses falsos. | **PASS** | `test_hu15_be_rechazo_json_malformado` |
| **Probar rechazo de campo faltante** | `[BE]` | La ausencia de un campo mandatorio (ej. `time_quality`, `seq_no`) provoca error de validación; se extrae la identidad para emitir ACK `rejected`. | **PASS** | `test_hu15_be_rechazo_campo_faltante` |
| **Probar schema_version no soportado** | `[BE]` | Versiones distintas a "1.0" son rechazadas de manera explícita indicando error de versión de esquema. | **PASS** | `test_hu15_be_rechazo_schema_version_no_soportado` |
| **Verificar que un rechazo no detiene el resto** | `[QA]` | Secuencias con mensajes inválidos intercalados entre mensajes legítimos no bloquean el pipeline: los válidos se persisten y los inválidos se rechazan aisladamente. | **PASS** | `test_hu15_qa_rechazo_no_detiene_el_resto` |

---

## 3. Logs de Ejecución

```text
backend/tests/test_hu15_validacion_entrante.py::test_hu15_be_aceptacion_null_mas_sensor_fault PASSED
backend/tests/test_hu15_validacion_entrante.py::test_hu15_be_rechazo_json_malformado PASSED
backend/tests/test_hu15_validacion_entrante.py::test_hu15_be_rechazo_campo_faltante PASSED
backend/tests/test_hu15_validacion_entrante.py::test_hu15_be_rechazo_schema_version_no_soportado PASSED
backend/tests/test_hu15_validacion_entrante.py::test_hu15_qa_rechazo_no_detiene_el_resto PASSED
============================== 5 passed in 1.12s ===============================
```
