# Evidencia — HU-31: Gráfica de tendencia térmica

Entorno: `infra-local/` (PostgreSQL 16, EMQX 5.8.6 con TLS 8883 y CA propia, backend FastAPI en Docker), nodo ESP32 DevKitC real con firmware ColdCare1023/IoT `develop` (v1.2.0) salvo donde se indique, frontend ColdCare1023/Frontend `main`. Fechas: 2–3 de octubre de 2026.

> Reemplaza la evidencia anterior de esta HU, que se apoyaba en pruebas sobre módulos simulados (cliente MQTT sin conexión real, broker y navegador falsos en Python) y no ejercitaba el sistema real.
## Defectos corregidos
- `GET /api/lecturas?limite=5000` respondía **422** (máximo 1000): la ventana de 24 h por defecto
  (~2880 lecturas) no cargaba. Límite alineado con el frontend (5000).
- La gráfica mezclaba lecturas de todos los dispositivos (ColdCare1023/Frontend: filtro y selector de equipo).

## Resultados (frontend real + ESP32)
| Tarea | Resultado |
|---|---|
| [FE] Ventana 24 h por defecto | ✅ Botón "24 h" con `aria-pressed=true` al entrar |
| [FE] Cambio de ventana | ✅ "1 h" pide `desde = ahora − 1 h`; "7 d" pide `desde = ahora − 7 d` |
| [FE] Datos reales | ✅ Curva del ESP32_FARMACIA_01 (~24,6 °C) con la banda 2–8 °C; el hueco coincide con el DS18B20 desconectado |
| Varios equipos | ✅ Selector "Equipo"; con otro nodo la petición lleva `device_id` y la curva solo muestra ese nodo |
