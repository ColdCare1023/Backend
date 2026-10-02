"""QA & Backend Validation Test Suite for HU-15: Inbound Telemetry Validation.

Checklist items covered:
  - [BE] Probar aceptación con null + SENSOR_FAULT
  - [BE] Probar rechazo de JSON malformado
  - [BE] Probar rechazo de campo faltante
  - [BE] Probar schema_version no soportado
  - [QA] Verificar que un rechazo no detiene el resto
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from pydantic import ValidationError

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "contrato"))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from telemetria_v1 import TelemetriaV1, parsear_telemetria
from consumidor_mqtt import ConsumidorMQTT

EJEMPLOS = Path(__file__).parent.parent / "contrato" / "ejemplos"


@pytest.fixture
def consumidor():
    c = ConsumidorMQTT(client_id="test-consumer-hu15")
    c.start()
    yield c
    c.stop()


def test_hu15_be_aceptacion_null_mas_sensor_fault(consumidor):
    """[BE] Payload con sensor caído (null + SENSOR_FAULT) se valida y procesa exitosamente."""
    raw = (EJEMPLOS / "telemetria_ds18b20_crc_error.json").read_bytes()
    telemetria = parsear_telemetria(raw)

    assert telemetria.temp_ds18b20 is None
    assert telemetria.ds18b20_status == "SENSOR_FAULT"
    assert telemetria.sensor_fault is True

    # El consumidor debe aceptarlo y comprometerlo en el almacén
    resultado = consumidor.process_message("farmacias/ESP32_FARMACIA_01/lecturas", raw)
    assert resultado["status"] == "committed"
    assert resultado["ident"] == ("ESP32_FARMACIA_01", 42, 1205)



def test_hu15_be_rechazo_json_malformado(consumidor):
    """[BE] Mensajes corruptos o con JSON inválido son rechazados."""
    # JSON roto sintácticamente
    malformado_sintaxis = b'{"device_id": "ESP32_FARMACIA_01", "boot_id": 42, "seq_no": 99, "incompleto":'
    res1 = consumidor.process_message("farmacias/ESP32_FARMACIA_01/lecturas", malformado_sintaxis)
    assert res1["status"] == "rejected"
    assert res1["ident"] is None  # No se puede extraer identidad de JSON roto

    # JSON no objeto
    res2 = consumidor.process_message("farmacias/ESP32_FARMACIA_01/lecturas", b'"cadena_suelta"')
    assert res2["status"] == "rejected"


def test_hu15_be_rechazo_campo_faltante(consumidor):
    """[BE] Payload al que le falta un campo mandatorio del contrato v1.0 se rechaza con ACK rejected."""
    d = json.loads((EJEMPLOS / "telemetria_normal.json").read_text(encoding="utf-8"))
    del d["time_quality"]  # Campo mandatorio faltante
    raw = json.dumps(d).encode("utf-8")

    res = consumidor.process_message("farmacias/ESP32_FARMACIA_01/lecturas", raw)
    assert res["status"] == "rejected"
    # La identidad se recupera para emitir el ACK lógico de rechazo
    assert res["ident"] == ("ESP32_FARMACIA_01", 42, 1205)
    assert "time_quality" in res["reason"]


def test_hu15_be_rechazo_schema_version_no_soportado(consumidor):
    """[BE] schema_version != '1.0' es explícitamente rechazado."""
    d = json.loads((EJEMPLOS / "telemetria_normal.json").read_text(encoding="utf-8"))
    d["schema_version"] = "2.0"
    raw = json.dumps(d).encode("utf-8")

    res = consumidor.process_message("farmacias/ESP32_FARMACIA_01/lecturas", raw)
    assert res["status"] == "rejected"
    assert res["ident"] == ("ESP32_FARMACIA_01", 42, 1205)
    assert "schema_version" in res["reason"]


def test_hu15_qa_rechazo_no_detiene_el_resto(consumidor):
    """[QA] Una secuencia intercalada de mensajes inválidos no bloquea ni aborta el consumo."""
    base_valido = json.loads((EJEMPLOS / "telemetria_normal.json").read_text(encoding="utf-8"))

    mensajes = [
        # 1. Válido
        (True, json.dumps({**base_valido, "seq_no": 1}).encode("utf-8")),
        # 2. JSON roto
        (False, b"{broken json"),
        # 3. Campo faltante
        (False, json.dumps({"device_id": "ESP32_FARMACIA_01", "boot_id": 42, "seq_no": 2}).encode("utf-8")),
        # 4. Válido
        (True, json.dumps({**base_valido, "seq_no": 3}).encode("utf-8")),
        # 5. Schema version inválido
        (False, json.dumps({**base_valido, "seq_no": 4, "schema_version": "99.0"}).encode("utf-8")),
        # 6. Válido con falla de sensor (null + SENSOR_FAULT)
        (True, (EJEMPLOS / "telemetria_ds18b20_crc_error.json").read_bytes()),
    ]

    resultados = []
    for es_valido_esperado, msg_bytes in mensajes:
        r = consumidor.process_message("farmacias/ESP32_FARMACIA_01/lecturas", msg_bytes)
        resultados.append(r)

    # Comprobamos que el consumidor procesó todos los mensajes sin detenerse
    assert len(resultados) == 6
    assert resultados[0]["status"] == "committed"
    assert resultados[1]["status"] == "rejected"
    assert resultados[2]["status"] == "rejected"
    assert resultados[3]["status"] == "committed"
    assert resultados[4]["status"] == "rejected"
    assert resultados[5]["status"] == "committed"

    health = consumidor.get_health_status()
    assert health["total_received"] == 6
    assert health["total_committed"] == 3
    assert health["total_rejected"] == 3
    assert health["status"] == "healthy"
