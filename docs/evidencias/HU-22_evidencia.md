# Evidencia de QA y Validación — HU-22: Persistencia del Historial Térmico (PostgreSQL)

## 1. Resumen de la Historia de Usuario
- **ID**: HU-22
- **Título**: Persistencia del historial térmico en PostgreSQL
- **Responsable**: Diego Soto
- **Objetivo**: Asegurar que toda telemetría válida se conserve de forma íntegra en la base de datos relacional (PostgreSQL) junto con su clasificación de IA cuando esté disponible. Se aplica una restricción de unicidad estricta `UNIQUE(device_id, boot_id, seq_no)` para impedir duplicados ante reintentos de red, auditando cualquier colisión con contenido discordante y garantizando que fallas de validación no contaminen el histórico térmico.

---

## 2. Checklist de Verificación y Resultados

| Tarea | Rol | Criterio Verificado | Estado | Evidencia |
|---|---|---|---|---|
| **Verificar persistencia en PostgreSQL real** | `[BE]` | Persistencia completa con tipos nativos de `device_id, boot_id, seq_no, captured_at, received_at, time_quality, uptime_s, temp_ds18b20, ds18b20_status, temp_sht31, humedad_sht31, sht31_status, door_open, door_open_s, mc38_status, sensor_fault, firmware_version`. Soporta almacenamiento complementario de predicciones IA sin alterar la telemetría original. | **PASS** | `test_hu22_be_persistencia_completa_en_bd`, `test_hu22_be_persistencia_con_clasificacion_ia` |
| **Probar idempotencia en PostgreSQL** | `[BE]` | La recepción repetida de la misma clave de identidad no genera filas duplicadas. Si el payload difiere, se audita en la tabla `conflictos_lectura`. | **PASS** | `test_hu22_be_idempotencia_en_bd` |
| **Confirmar que solo persiste lo validado** | `[QA]` | Mensajes con errores sintácticos de JSON o que incumplen las reglas de negocio del esquema Pydantic v1.0 son descartados en la capa de validación y nunca tocan la base de datos. | **PASS** | `test_hu22_qa_solo_persiste_lo_validado` |
| **Registrar evidencia** | `[QA]` | Ejecución de suite de integración con SQLAlchemy y generación del informe de pruebas. | **PASS** | Reporte registrado. |

---

## 3. Logs de Ejecución

```text
backend/tests/test_hu22_persistencia_postgresql.py::test_hu22_be_persistencia_completa_en_bd PASSED
backend/tests/test_hu22_persistencia_postgresql.py::test_hu22_be_persistencia_con_clasificacion_ia PASSED
backend/tests/test_hu22_persistencia_postgresql.py::test_hu22_be_idempotencia_en_bd PASSED
backend/tests/test_hu22_persistencia_postgresql.py::test_hu22_qa_solo_persiste_lo_validado PASSED
============================== 4 passed in 2.40s ===============================
```
