"""Prueba de formato JSON y validación de telemetría y eventos entrantes (HU-05, HU-13, HU-15).

Valida los payloads que genera el CÓDIGO REAL del firmware
(ColdCare1023/IoT: `scripts/generar_ejemplos_msvc.cmd` → `docs/ejemplos/`) contra el
esquema Pydantic v1.0, y comprueba que las mutaciones peligrosas se rechazan.

Uso:
    python validar_contrato.py            # informe legible, código de salida 0/1
    python -m pytest validar_contrato.py  # como suite de pruebas
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent))
from telemetria_v1 import EventoV1, TelemetriaV1, parsear_telemetria  # noqa: E402

EJEMPLOS = Path(__file__).parent / "ejemplos"


def _cargar(nombre: str) -> dict:
    return json.loads((EJEMPLOS / nombre).read_text(encoding="utf-8"))


def _rechaza(datos: dict) -> bool:
    try:
        TelemetriaV1.model_validate(datos)
    except ValidationError:
        return True
    return False


# ── Casos positivos: lo que el firmware emite debe pasar ─────────────────────

def test_todos_los_ejemplos_del_firmware_son_validos():
    archivos = sorted(EJEMPLOS.glob("*.json"))
    assert archivos, "No hay ejemplos: copiarlos de ColdCare1023/IoT docs/ejemplos"
    for ruta in archivos:
        cuerpo = ruta.read_bytes()
        if ruta.name.startswith("telemetria_"):
            parsear_telemetria(cuerpo)
        else:
            EventoV1.model_validate_json(cuerpo)


def test_falla_ds18b20_no_produce_temperatura_para_reglas():
    for nombre in ("telemetria_ds18b20_crc_error.json", "telemetria_ds18b20_desconectado.json"):
        t = TelemetriaV1.model_validate(_cargar(nombre))
        assert t.sensor_fault is True
        assert t.temperatura_valida_para_reglas() is None  # jamás una excursión falsa


def test_aislamiento_i2c_sht31_no_afecta_ds18b20():
    t = TelemetriaV1.model_validate(_cargar("telemetria_sht31_desconectado.json"))
    assert t.temp_sht31 is None and t.humedad_sht31 is None
    assert t.temperatura_valida_para_reglas() == 4.25


def test_lectura_simulada_no_alimenta_reglas_reales():
    t = TelemetriaV1.model_validate(_cargar("telemetria_demo_excursion_simulada.json"))
    assert t.simulated is True and t.temp_ds18b20 == 12.5
    assert t.temperatura_valida_para_reglas() is None


# ── Casos negativos: lo peligroso debe rechazarse ─────────────────────────────

def test_codigo_de_error_menos_127_se_rechaza():
    d = _cargar("telemetria_normal.json")
    d["temp_ds18b20"] = -127.0
    assert _rechaza(d)


def test_null_con_status_ok_se_rechaza():
    d = _cargar("telemetria_normal.json")
    d["temp_ds18b20"] = None
    assert _rechaza(d)


def test_valor_con_sensor_fault_se_rechaza():
    d = _cargar("telemetria_ds18b20_crc_error.json")
    d["temp_ds18b20"] = 4.0
    assert _rechaza(d)


def test_sensor_fault_inconsistente_se_rechaza():
    d = _cargar("telemetria_ds18b20_crc_error.json")
    d["sensor_fault"] = False
    assert _rechaza(d)


def test_humedad_fuera_de_0_100_se_rechaza():
    d = _cargar("telemetria_normal.json")
    d["humedad_sht31"] = 100.5
    assert _rechaza(d)


def test_schema_version_no_soportada_se_rechaza():
    d = _cargar("telemetria_normal.json")
    d["schema_version"] = "2.0"
    assert _rechaza(d)
    d["schema_version"] = 1  # contrato anterior
    assert _rechaza(d)


def test_campo_extra_se_rechaza():
    d = _cargar("telemetria_normal.json")
    d["received_at"] = "2026-09-25T15:30:01Z"  # lo agrega el backend, no el nodo
    assert _rechaza(d)


def test_campo_obligatorio_ausente_se_rechaza():
    for campo in ("seq_no", "boot_id", "temp_ds18b20", "time_quality"):
        d = _cargar("telemetria_normal.json")
        del d[campo]
        assert _rechaza(d), campo


def test_nan_se_rechaza():
    cuerpo = (EJEMPLOS / "telemetria_normal.json").read_text().replace('"temp_sht31":21.10', '"temp_sht31":NaN')
    try:
        parsear_telemetria(cuerpo)
    except (ValidationError, ValueError):
        return
    raise AssertionError("NaN aceptado")


def test_puerta_sin_mc38_se_rechaza():
    d = _cargar("telemetria_sin_mc38.json")
    d["door_open"] = True
    assert _rechaza(d)


def test_hora_null_con_calidad_sincronizada_se_rechaza():
    d = _cargar("telemetria_normal.json")
    d["captured_at"] = None
    assert _rechaza(d)


def test_device_id_con_caracteres_de_topico_se_rechaza():
    d = _cargar("telemetria_normal.json")
    d["device_id"] = "farmacias/+/lecturas"
    assert _rechaza(d)


def test_payload_sobredimensionado_se_rechaza():
    d = _cargar("telemetria_normal.json")
    grande = json.dumps(d) + " " * 800
    try:
        parsear_telemetria(grande)
    except ValueError:
        return
    raise AssertionError("payload > 768 B aceptado")


def _ejecutar() -> int:
    pruebas = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    fallos = 0
    for nombre, prueba in pruebas:
        try:
            prueba()
            print(f"PASS  {nombre}")
        except Exception as e:  # noqa: BLE001 — informe de QA, no se oculta nada
            fallos += 1
            print(f"FAIL  {nombre}: {e}")
    print(f"\n{len(pruebas) - fallos}/{len(pruebas)} pruebas de contrato OK")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(_ejecutar())
