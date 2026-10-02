# Evidencia de QA y Validación — HU-12: Recepción Continua de Telemetría en Backend

## 1. Resumen de la Historia de Usuario
- **ID**: HU-12
- **Título**: Recepción continua de telemetría en backend
- **Responsable**: Diego Soto
- **Objetivo**: Garantizar que el backend consuma de forma ininterrumpida la telemetría MQTT con QoS 1, manteniendo client_id fijo (`backend-consumer-prod`) y sesión persistente (`clean_session=False`), soportando múltiples dispositivos concurrentes sin bloqueos y ofreciendo observabilidad mediante el endpoint `/health`.

---

## 2. Checklist de Verificación y Resultados

| Tarea | Rol | Criterio Verificado | Estado | Evidencia |
|---|---|---|---|---|
| **Verificar suscripción al iniciar el servicio** | `[BE]` | FastAPI inicia el consumidor MQTT en su ciclo de vida (`lifespan`) con `client_id` fijo y `clean_session=False`, suscribiéndose a `farmacias/+/lecturas` con QoS 1. | **PASS** | `test_hu12_be_suscripcion_al_iniciar_servicio` |
| **Probar varios dispositivos simultáneos** | `[QA]` | Mensajes concurrentes emitidos por múltiples nodos (`ESP32_FARMACIA_01`, `02`, `03`, `DEMO_01`) son procesados en paralelo sin colisiones ni bloqueo entre dispositivos. | **PASS** | `test_hu12_qa_varios_dispositivos_simultaneos` |
| **Probar reconexión tras corte del broker** | `[QA]` | El consumidor detecta desconexión del broker (estado degradado), reconecta automáticamente con backoff y reanuda el consumo manteniendo sesión persistente sin intervención manual. | **PASS** | `test_hu12_qa_reconexion_tras_corte_del_broker` |
| **Probar API y consumo MQTT en paralelo** | `[QA]` | Las consultas HTTP a endpoints `/health` y `/api/v1/resumen` responden con éxito (código 200) de forma concurrente mientras el consumidor MQTT procesa ráfagas de telemetría. | **PASS** | `test_hu12_qa_api_y_consumo_mqtt_en_paralelo` |

---

## 3. Logs de Ejecución

```text
backend/tests/test_hu12_consumidor_mqtt.py::test_hu12_be_suscripcion_al_iniciar_servicio PASSED
backend/tests/test_hu12_consumidor_mqtt.py::test_hu12_qa_varios_dispositivos_simultaneos PASSED
backend/tests/test_hu12_consumidor_mqtt.py::test_hu12_qa_reconexion_tras_corte_del_broker PASSED
backend/tests/test_hu12_consumidor_mqtt.py::test_hu12_qa_api_y_consumo_mqtt_en_paralelo PASSED
========================= 4 passed in 3.99s =========================
```

### Respuesta del endpoint `/health`:
```json
{
  "status": "healthy",
  "mqtt_connected": true,
  "client_id": "backend-consumer-prod",
  "clean_session": false,
  "reconnect_count": 0,
  "last_message_at": "2026-10-02T16:59:45.120Z",
  "total_received": 50,
  "total_committed": 50,
  "total_duplicate": 0,
  "total_rejected": 0,
  "active_devices_count": 4,
  "active_devices": ["ESP32_DEMO_01", "ESP32_FARMACIA_01", "ESP32_FARMACIA_02", "ESP32_FARMACIA_03"]
}
```
