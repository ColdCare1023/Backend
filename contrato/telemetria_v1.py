"""Esquema Pydantic v2 del contrato de mensajes nodo ↔ backend, v1.0 (HU-07, HU-13, HU-15).

Espejo exacto de `src/core/Payload.cpp` y `docs/contrato_payload_v1.md` del repositorio
ColdCare1023/IoT. Pensado para que Diego lo
incorpore al backend (reemplazo de `LecturaPayload`) y para la "Prueba de
Formato JSON" del plan de QA.

Reglas que valida además de los tipos:
  - valor null  ⇔  *_status == "SENSOR_FAULT"  (nunca un código de error como número)
  - sensor_fault == (falla DS18B20 o falla SHT31)
  - captured_at null  ⇔  time_quality == "unsynced"
  - campos de puerta null  ⇔  mc38_status == "not_installed"
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.0"
PAYLOAD_MAX_BYTES = 768
DEVICE_ID_PATRON = r"^[A-Za-z0-9_-]{1,50}$"

Fallo = Literal["no_response", "crc_error", "timeout", "implausible", "power_on_reset"]
Estado = Literal["OK", "SENSOR_FAULT"]
UINT32 = 4_294_967_295


class TelemetriaV1(BaseModel):
    """Mensaje de `farmacias/<device_id>/lecturas`."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=False)

    schema_version: Literal["1.0"]
    device_id: str = Field(pattern=DEVICE_ID_PATRON)
    boot_id: int = Field(ge=0, le=UINT32)
    seq_no: int = Field(ge=1, le=UINT32)
    captured_at: datetime | None
    time_quality: Literal["sntp_synced", "sntp_backfilled", "unsynced"]
    uptime_s: int = Field(ge=0, le=UINT32)

    temp_ds18b20: float | None = Field(ge=-55.0, le=125.0)
    ds18b20_status: Estado
    ds18b20_fault: Fallo | None

    temp_sht31: float | None = Field(ge=-40.0, le=125.0)
    humedad_sht31: float | None = Field(ge=0.0, le=100.0)
    sht31_status: Estado
    sht31_fault: Fallo | None

    door_open: bool | None
    door_open_s: int | None = Field(ge=0, le=65535)
    door_open_streak_s: int | None = Field(ge=0, le=65535)
    mc38_status: Literal["installed", "not_installed"]

    power_source: Literal["mains", "backup"] | None
    sensor_fault: bool
    firmware_version: str = Field(pattern=r"^v\d{1,3}\.\d{1,3}\.\d{1,3}$")
    simulated: Literal[True] | None = None

    @model_validator(mode="after")
    def _coherencia(self) -> "TelemetriaV1":
        # DS18B20: valor presente ⇔ OK ⇔ sin motivo de falla.
        ds_ok = self.ds18b20_status == "OK"
        if ds_ok != (self.temp_ds18b20 is not None) or ds_ok != (self.ds18b20_fault is None):
            raise ValueError("DS18B20 incoherente: valor/status/fault no concuerdan")

        sht_ok = self.sht31_status == "OK"
        ambos = self.temp_sht31 is not None and self.humedad_sht31 is not None
        if sht_ok != ambos or sht_ok != (self.sht31_fault is None):
            raise ValueError("SHT31 incoherente: valores/status/fault no concuerdan")

        if self.sensor_fault != (not ds_ok or not sht_ok):
            raise ValueError("sensor_fault no refleja el estado de los sensores")

        if (self.captured_at is None) != (self.time_quality == "unsynced"):
            raise ValueError("captured_at null solo con time_quality = unsynced")

        sin_mc38 = self.mc38_status == "not_installed"
        puerta = (self.door_open, self.door_open_s, self.door_open_streak_s)
        if sin_mc38 and any(v is not None for v in puerta):
            raise ValueError("sin MC-38 los campos de puerta deben ser null")
        if not sin_mc38 and (self.door_open is None or self.door_open_s is None):
            raise ValueError("con MC-38 door_open y door_open_s son obligatorios")
        return self

    # ── Reglas de dominio que el backend debe aplicar ────────────────────
    def temperatura_valida_para_reglas(self) -> float | None:
        """Temperatura utilizable por la regla 2–8 °C y por la IA.

        None si el DS18B20 está en falla o si la lectura es simulada: nunca se
        calcula una excursión a partir de un código de error (HU-21 esc. 4).
        """
        if self.ds18b20_status != "OK" or self.simulated:
            return None
        return self.temp_ds18b20


class AckLogicoV1(BaseModel):
    """Mensaje que el backend publica en `farmacias/<device_id>/ack` tras el COMMIT."""

    model_config = ConfigDict(extra="allow")

    device_id: str = Field(pattern=DEVICE_ID_PATRON)
    boot_id: int = Field(ge=0, le=UINT32)
    seq_no: int = Field(ge=1, le=UINT32)
    status: Literal["committed", "duplicate", "rejected"]


class EventoV1(BaseModel):
    """Mensaje de `farmacias/<device_id>/eventos` (incluido el LWT)."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    schema_version: Literal["1.0"]
    device_id: str = Field(pattern=DEVICE_ID_PATRON)
    event_type: Literal[
        "arranque", "online", "lwt_offline", "buffer_80", "buffer_saturado",
        "registros_en_cuarentena", "wifi_reconexion_prolongada", "credencial_rotada",
        "conmutacion_respaldo", "suministro_restablecido", "respaldo_autonomia_critica",
        "estado_nodo",
    ]
    # El LWT lo publica el broker: no lleva boot_id, hora ni detalle.
    boot_id: int | None = Field(default=None, ge=0, le=UINT32)
    occurred_at: datetime | None = None
    time_quality: Literal["sntp_synced", "sntp_backfilled", "unsynced"] | None = None
    uptime_s: int | None = Field(default=None, ge=0, le=UINT32)
    detail: dict | None = None

    @model_validator(mode="after")
    def _lwt_minimo(self) -> "EventoV1":
        if self.event_type != "lwt_offline" and (self.boot_id is None or self.time_quality is None):
            raise ValueError("los eventos del nodo llevan boot_id y time_quality")
        return self


def parsear_telemetria(cuerpo: bytes | str) -> TelemetriaV1:
    """Valida el cuerpo crudo de un mensaje MQTT, incluido el tope de tamaño."""
    datos = cuerpo.encode() if isinstance(cuerpo, str) else cuerpo
    if len(datos) > PAYLOAD_MAX_BYTES:
        raise ValueError(f"payload de {len(datos)} B excede {PAYLOAD_MAX_BYTES} B")
    return TelemetriaV1.model_validate(json.loads(datos))
