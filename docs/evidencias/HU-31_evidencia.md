# Evidencia de QA y Validación — HU-31: Visualización de Tendencia Térmica

## 1. Resumen de la Historia de Usuario
- **ID**: HU-31
- **Título**: Visualización de Tendencia Térmica
- **Responsable**: Diego Soto
- **Objetivo**: Proveer una gráfica de temperatura interactiva con umbrales fijos de 2 °C a 8 °C (zonas seguras delimitadas visualmente), cargando por defecto las últimas 24 horas con selector de ventana (6h, 12h, 24h, 7d), estado vacío informativo para dispositivos sin historial y muestreo visual (downsampling LTTB) para garantizar fluidez ante miles de registros continuos.

---

## 2. Checklist de Verificación y Resultados

| Tarea | Rol | Criterio Verificado | Estado | Evidencia |
|---|---|---|---|---|
| **Verificar ventana de 24 h por defecto** | `[FE]` | Si el técnico no selecciona rango, la consulta carga automáticamente las últimas 24 horas (`hasta - desde == 24 h`). | **PASS** | `test_hu31_fe_ventana_de_24_horas_por_defecto` |
| **Verificar cambio de ventana** | `[FE]` | La interfaz y el servicio soportan rangos dinámicos configurables (6h, 12h, 48h, 7 días / 168h). | **PASS** | `test_hu31_fe_cambio_de_ventana` |
| **Probar con datos reales** | `[FE]` | Las lecturas reales se renderizan superpuestas sobre las bandas objetivo de 2.0 a 8.0 °C (verde) y zonas de riesgo de congelación (<2 °C) y calentamiento (>8 °C) en rojo. | **PASS** | `test_hu31_fe_probar_con_datos_reales_y_zonas_2_8` |
| **Registrar evidencia de escenarios** | `[QA]` | Cobertura de 3 escenarios clave: carga con datos, empty state informativo ante nodo sin transmisiones, y downsampling automático en volúmenes masivos (2000 puntos reducidos a <= 500). | **PASS** | `test_hu31_qa_escenario_sin_datos_empty_state`, `test_hu31_qa_escenario_gran_volumen_downsampling` |

---

## 3. Logs de Ejecución

```text
backend/tests/test_hu31_tendencia_termica.py::test_hu31_fe_ventana_de_24_horas_por_defecto PASSED
backend/tests/test_hu31_tendencia_termica.py::test_hu31_fe_cambio_de_ventana PASSED
backend/tests/test_hu31_tendencia_termica.py::test_hu31_fe_probar_con_datos_reales_y_zonas_2_8 PASSED
backend/tests/test_hu31_tendencia_termica.py::test_hu31_qa_escenario_sin_datos_empty_state PASSED
backend/tests/test_hu31_tendencia_termica.py::test_hu31_qa_escenario_gran_volumen_downsampling PASSED
========================= 5 passed in 1.59s =========================
```
