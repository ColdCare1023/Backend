"""El SHT31 puede fallar en una sola magnitud (CRC malo solo en la palabra de
humedad, o solo en la de temperatura). El firmware envía entonces la magnitud
válida con `sht31_status = SENSOR_FAULT`; la lectura no debe rechazarse, porque
arrastraría a cuarentena la temperatura interna válida del DS18B20."""

import json
from datetime import datetime, timezone
from pathlib import Path

from src.infrastructure.mqtt import contrato_v1
from src.infrastructure.mqtt.payload_schema import LecturaPayload

EJEMPLOS = Path(__file__).resolve().parent.parent / "fixtures" / "contrato_v1"


def _telemetria(**cambios) -> contrato_v1.TelemetriaV1:
    datos = json.loads((EJEMPLOS / "telemetria_normal.json").read_text(encoding="utf-8"))
    datos.update(cambios)
    return contrato_v1.TelemetriaV1.model_validate(datos)


def _interna(telemetria: contrato_v1.TelemetriaV1) -> LecturaPayload:
    return LecturaPayload.model_validate(
        contrato_v1.a_lectura_interna(telemetria, datetime.now(timezone.utc))
    )


def test_humedad_en_fallo_conserva_la_temperatura_ambiental_valida():
    telemetria = _telemetria(
        humedad_sht31=None, sht31_status="SENSOR_FAULT", sht31_fault="crc_error", sensor_fault=True
    )

    lectura = _interna(telemetria)

    assert lectura.temperatura_ambiental == telemetria.temp_sht31
    assert lectura.estado_temperatura_ambiental == "ok"
    assert lectura.humedad_ambiental is None
    assert lectura.estado_humedad_ambiental == "sensor_error"
    assert lectura.temperatura_interna == telemetria.temp_ds18b20


def test_temperatura_ambiental_en_fallo_conserva_la_humedad_valida():
    telemetria = _telemetria(
        temp_sht31=None, sht31_status="SENSOR_FAULT", sht31_fault="crc_error", sensor_fault=True
    )

    lectura = _interna(telemetria)

    assert lectura.temperatura_ambiental is None
    assert lectura.estado_temperatura_ambiental == "sensor_error"
    assert lectura.humedad_ambiental == telemetria.humedad_sht31
    assert lectura.estado_humedad_ambiental == "ok"
