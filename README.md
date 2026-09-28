# ColdCare Backend — contrato de mensajes y herramientas del canal MQTT

Lado servidor del contrato entre el nodo IoT (repo **ColdCare1023/IoT**) y el backend: esquemas Pydantic v2
listos para la ingesta FastAPI, un backend simulado con acuse lógico y un banco MQTT/TLS local para probar
el firmware sin depender del servicio en la nube. Historia organizada en **un commit por historia de usuario**.

| Carpeta | Contenido | HU |
|---|---|---|
| [`contrato/`](contrato/README.md) | `TelemetriaV1`, `EventoV1`, `AckLogicoV1` + ejemplos generados por el firmware real + 17 pruebas | HU-15, HU-13, HU-07 |
| [`banco_mqtt_local/`](banco_mqtt_local/README.md) | Backend simulado (SQLite, `UNIQUE(device_id, boot_id, seq_no)`, acuse tras commit) y broker mosquitto con TLS 8883 + ACL | HU-07, HU-11, HU-10 |
| [`simulacion_excursiones/`](simulacion_excursiones/README.md) | Excursiones SIMULADAS para la demostración (`simulated: true`, `ESP32_DEMO_01`) | HU-21 |

## Contrato en una línea por mensaje

| Tópico | Sentido | Mensaje |
|---|---|---|
| `farmacias/<device_id>/lecturas` | nodo → backend | `TelemetriaV1` (QoS 1) |
| `farmacias/<device_id>/eventos` | nodo → backend | `EventoV1` (incluye el LWT `lwt_offline`) |
| `farmacias/<device_id>/ack` | backend → nodo | `{"device_id", "boot_id", "seq_no", "status": committed/duplicate/rejected}` **después** del COMMIT |

Reglas para la ingesta: deduplicar por `(device_id, boot_id, seq_no)`; responder `rejected` también a
payloads inválidos que traigan identidad (si no, el nodo reintenta para siempre); no publicar acuse si el
COMMIT falla; usar `temperatura_valida_para_reglas()` como única entrada de la regla 2–8 °C y de la IA.

## Uso

```bash
pip install -r requirements.txt
python contrato/validar_contrato.py
python simulacion_excursiones/simular_excursion.py --dry-run
```

## Verificado (28/09/2026)

- Contrato: 17/17 pruebas con los mensajes generados por el firmware.
- Extremo a extremo con broker TLS local: perfil `excursion` 13/13 acuses, `falla_sensor` 7/7 (sin
  excursión por falla), modo `sin_ack` 0/6, token inválido → `Not authorized`.
