"""QA & Backend Validation Test Suite for HU-05: Versioned Telemetry Structuring.

Checklist items covered:
  - [FW] Verificar campos del payload real (validación de campos v1.0)
  - [FW] Probar sensor caído como null + SENSOR_FAULT
  - [FW] Probar rechazo por tamaño excedido (> 768 B)
  - [BE] Verificar received_at en recepción (asignación de timestamp UTC por el backend)
  - [QA] Registrar evidencia de escenarios
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent / "contrato"))
from telemetria_v1 import PAYLOAD_MAX_BYTES, TelemetriaV1, parsear_telemetria

EJEMPLOS = Path(__file__).parent.parent / "contrato" / "ejemplos"


class LecturaIngestadaBackend(BaseModel):
    """Modelo del backend que preserva el contrato del nodo y agrega metadata del servidor."""
    model_config = ConfigDict(extra="forbid")

    telemetria: TelemetriaV1
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def received_at_iso(self) -> str:
        return self.received_at.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_hu05_campos_del_payload_real():
    """[FW] + [BE] Verifica que todos los campos del payload v1.0 emitido por el nodo son parseados."""
    datos_crudos = (EJEMPLOS / "telemetria_normal.json").read_bytes()
    telemetria = parsear_telemetria(datos_crudos)

    # Campos esenciales identificadores
    assert telemetria.schema_version == "1.0"
    assert telemetria.device_id == "ESP32_FARMACIA_01"
    assert telemetria.boot_id == 42
    assert telemetria.seq_no == 1205
    assert telemetria.captured_at is not None
    assert telemetria.time_quality == "sntp_synced"
    assert telemetria.uptime_s == 36000

    # Sensores internos y ambientales
    assert telemetria.temp_ds18b20 == 4.25
    assert telemetria.ds18b20_status == "OK"
    assert telemetria.ds18b20_fault is None

    assert telemetria.temp_sht31 == 21.10
    assert telemetria.humedad_sht31 == 58.50
    assert telemetria.sht31_status == "OK"
    assert telemetria.sht31_fault is None

    # Estado de puerta y auxiliares
    assert telemetria.door_open is False
    assert telemetria.mc38_status == "installed"
    assert telemetria.sensor_fault is False
    assert telemetria.firmware_version == "v1.2.0"


def test_hu05_sensor_caido_null_y_sensor_fault():
    """[FW] + [BE] Falla de sensor debe representarse como null y status SENSOR_FAULT."""
    datos_ds_fail = (EJEMPLOS / "telemetria_ds18b20_crc_error.json").read_bytes()
    telemetria_ds = parsear_telemetria(datos_ds_fail)
    assert telemetria_ds.temp_ds18b20 is None
    assert telemetria_ds.ds18b20_status == "SENSOR_FAULT"
    assert telemetria_ds.ds18b20_fault == "crc_error"
    assert telemetria_ds.sensor_fault is True
    # Temperatura no utilizable para reglas
    assert telemetria_ds.temperatura_valida_para_reglas() is None

    # SHT31 caído aislado
    datos_sht_fail = (EJEMPLOS / "telemetria_sht31_desconectado.json").read_bytes()
    telemetria_sht = parsear_telemetria(datos_sht_fail)
    assert telemetria_sht.temp_sht31 is None
    assert telemetria_sht.humedad_sht31 is None
    assert telemetria_sht.sht31_status == "SENSOR_FAULT"
    assert telemetria_sht.sensor_fault is True
    # DS18B20 sigue operando normalmente
    assert telemetria_sht.temp_ds18b20 == 4.25
    assert telemetria_sht.ds18b20_status == "OK"


def test_hu05_rechazo_por_tamano_excedido():
    """[FW] + [BE] Payloads que excedan 768 B deben ser rechazados sin procesar."""
    datos_validos = (EJEMPLOS / "telemetria_normal.json").read_text(encoding="utf-8")
    payload_excedido = datos_validos + (" " * (PAYLOAD_MAX_BYTES - len(datos_validos) + 10))
    assert len(payload_excedido.encode("utf-8")) > PAYLOAD_MAX_BYTES

    with pytest.raises(ValueError, match="excede"):
        parsear_telemetria(payload_excedido)


def test_hu05_received_at_en_recepcion():
    """[BE] Al llegar el mensaje MQTT, el backend asigna received_at en UTC."""
    datos_crudos = (EJEMPLOS / "telemetria_normal.json").read_bytes()
    telemetria = parsear_telemetria(datos_crudos)

    # Ingesta en el backend
    t_antes = datetime.now(timezone.utc)
    registro_backend = LecturaIngestadaBackend(telemetria=telemetria)
    t_despues = datetime.now(timezone.utc)

    assert registro_backend.received_at is not None
    assert registro_backend.received_at.tzinfo == timezone.utc
    assert t_antes <= registro_backend.received_at <= t_despues
    # El nodo NO debe enviar received_at (extra='forbid' lo rechazaría)
    d = json.loads(datos_crudos)
    d["received_at"] = "2026-09-25T15:30:00Z"
    with pytest.raises(ValidationError):
        TelemetriaV1.model_validate(d)
