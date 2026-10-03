# Evidencia — HU-04: Detección de apertura de puerta (MC-38) — lado backend

Entorno: `infra-local/` (PostgreSQL 16, EMQX 5.8.6 con TLS 8883 y CA propia, backend FastAPI en Docker), nodo ESP32 DevKitC real con firmware ColdCare1023/IoT `develop` (v1.2.0) salvo donde se indique, frontend ColdCare1023/Frontend `main`. Fechas: 2–3 de octubre de 2026.

## Defecto corregido
Una lectura sin MC-38 (`door_open: null`, `mc38_status: not_installed`) se guardaba como
`apertura_refrigerador = false`: el ORM aplicaba el `default=False` de la columna a un `None` explícito y el
nodo aparecía con la puerta **cerrada** en vez de "no aplica".

## Verificación
- `tests/integration/test_hu05_hu07_payload_y_ack.py::test_nodo_sin_mc38_persiste_puerta_como_null`
- `tests/integration/test_contrato_v1_coldcare.py::test_sin_mc38_la_puerta_queda_null` (mensaje real del firmware)
- En vivo, nodo compilado sin MC-38: la fila queda `apertura_refrigerador IS NULL`.

| device_id | apertura_refrigerador |
|---|---|
| nodo sin MC-38 | NULL (antes: `false`) |
| nodo con MC-38, puerta cerrada | `false` |

La evidencia de hardware de la HU (apertura real, antirrebote, sin MC-38) está en ColdCare1023/IoT
`docs/evidencias/HU-04_evidencia.md`. Pin del MC-38: GPIO 27.
