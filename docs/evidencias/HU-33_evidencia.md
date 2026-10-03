# Evidencia — HU-33: Tarjetas KPI

Entorno: `infra-local/` (PostgreSQL 16, EMQX 5.8.6 con TLS 8883 y CA propia, backend FastAPI en Docker), nodo ESP32 DevKitC real con firmware ColdCare1023/IoT `develop` (v1.2.0) salvo donde se indique, frontend ColdCare1023/Frontend `main`. Fechas: 2–3 de octubre de 2026.

> Reemplaza la evidencia anterior de esta HU, que se apoyaba en pruebas sobre módulos simulados (cliente MQTT sin conexión real, broker y navegador falsos en Python) y no ejercitaba el sistema real.
## Defectos corregidos
- `GET /api/dispositivos/estado-operativo` no existía (404): se implementa (OPERATIVO / SIN_DATOS /
  FALLA_SENSOR a partir de la última lectura recibida; `?todos=true` para el selector de equipo).
- La tarjeta tomaba el estado de un id fijo de demo (`FARM-01-CDL`) y la última lectura de cualquier nodo;
  ahora usa el equipo seleccionado.

## Resultados (frontend real + ESP32)
| Tarea | Resultado |
|---|---|
| [FE] Estado actual con datos reales | ✅ 24,6 °C con "Excursión crítica" (sensor a temperatura ambiente), puerta, conexión, 25,5 °C y 70 % HR |
| [FE] Estado de falla de sensor | ✅ DS18B20 desconectado: "—" y "No hay lectura de temperatura. Revisa el sensor."; sin repetir el último valor ni marcar excursión; ambiente y humedad siguen visibles |

Prueba backend: `tests/integration/test_hu33_estado_operativo.py`.
