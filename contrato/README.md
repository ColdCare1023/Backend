# Contrato de mensajes v1.0 — esquema y verificación

| Archivo | Qué es |
|---|---|
| `telemetria_v1.py` | Modelos Pydantic v2 del contrato nodo ↔ backend, con reglas cruzadas: valor `null` ⇔ `SENSOR_FAULT`, `sensor_fault` coherente, hora `null` ⇔ `unsynced`, puerta `null` ⇔ sin MC-38 |
| `ejemplos/*.json` | Mensajes generados por el **código real del firmware** (ColdCare1023/IoT, `docs/ejemplos/`) |
| `validar_contrato.py` | Ejemplos válidos + mutaciones peligrosas rechazadas (−127, NaN, `null` con OK, versión no soportada, campo extra, > 768 B…) |

`TelemetriaV1` reemplaza al esquema de ingesta anterior. `TelemetriaV1.temperatura_valida_para_reglas()`
devuelve `None` ante falla de sensor o lectura simulada: es el único valor que debe alimentar la regla
2–8 °C y la IA (HU-21 esc. 4). Especificación completa: ColdCare1023/IoT `docs/contrato_payload_v1.md`.

```bash
python validar_contrato.py            # informe
python -m pytest validar_contrato.py  # como suite
```
