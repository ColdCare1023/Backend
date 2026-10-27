"""El script de banco de HU-44 publica telemetría que el backend acepta."""

import json

import pytest

from scripts import pruebas_hu44_banco as banco
from src.infrastructure.mqtt import contrato_v1


def test_la_telemetria_de_banco_cumple_el_contrato_v1():
    datos = json.loads(banco._telemetria("ESP32_FARMACIA_01", 11.5, 7))
    lectura = contrato_v1.TelemetriaV1.model_validate(datos)
    assert lectura.boot_id == 999999 and lectura.temp_ds18b20 == 11.5


def test_sin_subcomando_termina_con_error():
    with pytest.raises(SystemExit):
        banco.main([])
