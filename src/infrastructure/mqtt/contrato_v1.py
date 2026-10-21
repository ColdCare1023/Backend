"""Contrato de mensajes nodo ↔ backend v1.0 (firmware ColdCare1023/IoT).

`TelemetriaV1`, `EventoV1` y `AckLogicoV1` son copia de
ColdCare1023/Backend `contrato/telemetria_v1.py`, que a su vez es espejo de
`src/core/Payload.cpp` y `docs/contrato_payload_v1.md` del firmware. Si el
contrato cambia, se cambia allí primero y se copia aquí.

Lo propio de este módulo es la traducción al modelo interno del backend
(`LecturaPayload`): el resto del pipeline —IA, alertas, cadena de hash, SSE,
reportes— no distingue entre el contrato anterior y v1.0.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.domain.value_objects.device_id import PATRON_DEVICE_ID

SCHEMA_VERSION = "1.0"
PAYLOAD_MAX_BYTES = 768
MAX_CLAVES_DETALLE = 32
MAX_BYTES_DETALLE = 1024
DEVICE_ID_PATRON = PATRON_DEVICE_ID

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
    def _coherencia(self) -> TelemetriaV1:
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


class EventoV1(BaseModel):
    """Mensaje de `farmacias/<device_id>/eventos` (incluido el LWT)."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    schema_version: Literal["1.0"]
    device_id: str = Field(pattern=DEVICE_ID_PATRON)
    event_type: Literal[
        "arranque", "online", "lwt_offline", "buffer_80", "buffer_saturado",
        "registros_en_cuarentena", "wifi_reconexion_prolongada", "credencial_rotada", "puerta",
        "conmutacion_respaldo", "suministro_restablecido", "respaldo_autonomia_critica",
        "estado_nodo",
        # IOT-002: auditoría de la consola de aprovisionamiento del nodo.
        "config_modificada", "reinicio_manual", "consola_desbloqueada", "consola_desbloqueo_fallido",
    ]
    # El LWT lo publica el broker: no lleva boot_id, hora ni detalle.
    boot_id: int | None = Field(default=None, ge=0, le=UINT32)
    occurred_at: datetime | None = None
    time_quality: Literal["sntp_synced", "sntp_backfilled", "unsynced"] | None = None
    uptime_s: int | None = Field(default=None, ge=0, le=UINT32)
    detail: dict | None = None

    @model_validator(mode="after")
    def _detalle_acotado(self) -> EventoV1:
        # BE-019: el detalle termina en audit_logs y en la cadena; un nodo no
        # puede inflarlos con estructuras arbitrarias.
        if self.detail is not None:
            if len(self.detail) > MAX_CLAVES_DETALLE or len(json.dumps(self.detail)) > MAX_BYTES_DETALLE:
                raise ValueError("detail excede el tamaño admitido")
        return self

    @model_validator(mode="after")
    def _lwt_minimo(self) -> EventoV1:
        if self.event_type != "lwt_offline" and (self.boot_id is None or self.time_quality is None):
            raise ValueError("los eventos del nodo llevan boot_id y time_quality")
        return self


# ── Traducción al modelo interno ──────────────────────────────────────────

# `sntp_backfilled`: el nodo capturó sin hora y la fechó después con el uptime
# del mismo arranque. Ya trae hora estimada; no necesita el reanclaje del
# backend, que solo actúa sobre las `unsynced`.
TIME_QUALITY_INTERNO = {"sntp_synced": "synced", "sntp_backfilled": "backfilled", "unsynced": "unsynced"}

# Estado interno del acuse → `status` que entiende el firmware v1.0.
# `rejected` hace que el nodo mueva el registro a cuarentena (no lo borra).
# BE-001: un conflicto de payload (misma identidad, contenido distinto) NO se
# acusa como `duplicate`: eso autorizaba al nodo a borrar su única copia de una
# lectura que el backend no tiene. Va a cuarentena, donde se conserva como
# evidencia, y el conflicto queda auditado en el backend.
STATUS_ACK = {
    "commit_confirmado": "committed",
    "duplicado": "duplicate",
    "conflicto_payload": "rejected",
    "payload_rechazado": "rejected",
    "lectura_invalida": "rejected",
    "dispositivo_no_autorizado": "rejected",
}


def es_v1(crudo: object) -> bool:
    return isinstance(crudo, dict) and crudo.get("schema_version") == SCHEMA_VERSION


def a_lectura_interna(t: TelemetriaV1, recibido: datetime) -> dict:
    """Campos de `LecturaPayload` equivalentes a una telemetría v1.0.

    Sin hora del nodo (`unsynced`, `captured_at` null) se usa el instante de
    recepción como `timestamp` provisional: la lectura conserva
    `time_quality = unsynced` y `uptime_ms`, y el backend reconstruye su hora
    real en `timestamp_estimado` cuando llega una lectura sincronizada del mismo
    arranque (ver `SQLAlchemyLecturaRepository.reanclar_no_sincronizadas`).
    """
    ds_ok = t.ds18b20_status == "OK"
    sht_ok = t.sht31_status == "OK"
    return {
        "device_id": t.device_id,
        "timestamp": t.captured_at or recibido,
        "schema_version": 1,
        "reading_id": f"{t.device_id}_{t.boot_id}_{t.seq_no}",
        "boot_id": t.boot_id,
        "seq_no": t.seq_no,
        "uptime_ms": t.uptime_s * 1000,
        "time_quality": TIME_QUALITY_INTERNO[t.time_quality],
        "temperatura_interna": t.temp_ds18b20,
        "estado_temperatura_interna": "ok" if ds_ok else "sensor_error",
        "temperatura_ambiental": t.temp_sht31,
        "estado_temperatura_ambiental": "ok" if sht_ok else "sensor_error",
        "humedad_ambiental": t.humedad_sht31,
        "estado_humedad_ambiental": "ok" if sht_ok else "sensor_error",
        "apertura_refrigerador": t.door_open,
        "mc38_status": "ok" if t.mc38_status == "installed" else "not_installed",
        "duracion_apertura_segundos": t.door_open_s or 0,
        "estado_conectividad": "online",
        "firmware_version": t.firmware_version,
    }


def evidencia_v1(t: TelemetriaV1) -> dict:
    """Campos de v1.0 sin columna propia: se conservan en `payload` (evidencia)."""
    return {
        "contrato": SCHEMA_VERSION,
        "time_quality_nodo": t.time_quality,
        "sensor_fault": t.sensor_fault,
        "ds18b20_fault": t.ds18b20_fault,
        "sht31_fault": t.sht31_fault,
        "door_open_streak_s": t.door_open_streak_s,
        "power_source": t.power_source,
        "simulated": bool(t.simulated),
    }
