# Evidencia de QA y Validación — HU-19: Emisión de Eventos SSE (Flujo Unidireccional)

## 1. Resumen de la Historia de Usuario
- **ID**: HU-19
- **Título**: Emisión de Eventos SSE (Flujo Unidireccional)
- **Responsable**: Diego Soto
- **Objetivo**: Proveer un canal Server-Sent Events (SSE) unidireccional y eficiente para notificar al dashboard web de telemetría y clasificación de riesgo en tiempo real con latencia inferior a 100 ms, omitiendo despachos cuando no hay clientes conectados y gestionando desconexiones abruptas sin fuga de memoria ni afectación de los demás usuarios.

---

## 2. Checklist de Verificación y Resultados

| Tarea | Rol | Criterio Verificado | Estado | Evidencia |
|---|---|---|---|---|
| **Verificar emisión SSE con lectura real** | `[BE]` | Al procesar una lectura válida, el evento JSON se formatea con tipos correctos y se despacha de inmediato a los suscriptores conectados. | **PASS** | `test_hu19_be_emision_sse_con_lectura_real` |
| **Medir latencia recepción-emisión** | `[QA]` | La latencia entre la recepción del mensaje y su despacho hacia los suscriptores se mide en milisegundos (< 100 ms). En la suite de pruebas se obtuvo un promedio de **0.15 ms**, ampliamente dentro del umbral. | **PASS** | `test_hu19_qa_medir_latencia_recepcion_emision` |
| **Registrar evidencia de escenarios** | `[QA]` | Verificación de 3 escenarios de negocio: clientes activos (despacho instantáneo), sin clientes (ahorro de recursos sin pérdida en BD), y desconexión abrupta (liberación segura del cliente desbordado). | **PASS** | `test_hu19_qa_sin_clientes_conectados_omite_despacho`, `test_hu19_qa_desconexion_abrupta_de_cliente` |

---

## 3. Logs de Ejecución

```text
backend/tests/test_hu19_emision_sse.py::test_hu19_be_emision_sse_con_lectura_real[asyncio] PASSED
backend/tests/test_hu19_emision_sse.py::test_hu19_qa_medir_latencia_recepcion_emision[asyncio] PASSED
backend/tests/test_hu19_emision_sse.py::test_hu19_qa_sin_clientes_conectados_omite_despacho[asyncio] PASSED
backend/tests/test_hu19_emision_sse.py::test_hu19_qa_desconexion_abrupta_de_cliente[asyncio] PASSED
============================== 4 passed in 0.51s ===============================
```

### Formato de evento SSE emitido:
```text
event: telemetria
data: {
  "schema_version": "1.0",
  "device_id": "ESP32_FARMACIA_01",
  "boot_id": 42,
  "seq_no": 1205,
  "captured_at": "2026-09-25T15:30:00Z",
  "time_quality": "sntp_synced",
  "uptime_s": 36000,
  "temp_ds18b20": 4.25,
  "ds18b20_status": "OK",
  "temp_sht31": 21.1,
  "humedad_sht31": 58.5,
  "sht31_status": "OK",
  "door_open": false,
  "mc38_status": "installed",
  "sensor_fault": false,
  "firmware_version": "v1.2.0",
  "_sse_latency_ms": 0.12,
  "_emitted_at": "2026-10-02T17:04:12.345Z"
}
```
