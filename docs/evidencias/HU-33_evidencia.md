# Evidencia de QA y Validación — HU-33: Tarjetas de KPI (Estado Actual y Tolerancia a Fallo)

## 1. Resumen de la Historia de Usuario
- **ID**: HU-33
- **Título**: Tarjetas de KPI (Estado Actual y Tolerancia a Fallo)
- **Responsable**: Diego Soto
- **Objetivo**: Renderizar en el dashboard web el último valor exacto de temperatura (°C) y humedad relativa (% HR) de forma clara e intuitiva. Si el flujo de telemetría indica que un sensor particular está en falla (`null` + `SENSOR_FAULT`), la tarjeta muestra explícitamente "Falla de sensor" o "Sin lectura disponible", impidiendo que valores numéricos inventados o 0.0 engañen al personal. Asimismo, si no se reciben datos durante más de 5 minutos, la tarjeta visualiza una advertencia de dato desactualizado por corte de red.

---

## 2. Checklist de Verificación y Resultados

| Tarea | Rol | Criterio Verificado | Estado | Evidencia |
|---|---|---|---|---|
| **Verificar estado actual con datos reales** | `[FE]` | Muestra valores numéricos exactos en pantalla (ej. "4.25 °C" y "58.50 % HR") con datos vigentes y estado visual normal. | **PASS** | `test_hu33_fe_estado_actual_con_datos_reales` |
| **Verificar estado de falla de sensor** | `[FE]` | Si el sensor reporta `null` y `SENSOR_FAULT`, el KPI muestra texto explícito ("Falla de sensor") y alerta visual, evitando desplegar ceros o información falsa. | **PASS** | `test_hu33_fe_estado_de_falla_de_sensor` |
| **Registrar evidencia** | `[QA]` | Comprobación de 3 estados de la interfaz: vigente, desactualizado por antigüedad de red (> 300 s) y falla de sensor. | **PASS** | `test_hu33_qa_lectura_desactualizada_por_red` |

---

## 3. Logs de Ejecución

```text
backend/tests/test_hu33_tarjetas_kpi.py::test_hu33_fe_estado_actual_con_datos_reales PASSED
backend/tests/test_hu33_tarjetas_kpi.py::test_hu33_fe_estado_de_falla_de_sensor PASSED
backend/tests/test_hu33_tarjetas_kpi.py::test_hu33_qa_lectura_desactualizada_por_red PASSED
============================== 3 passed in 0.40s ===============================
```
