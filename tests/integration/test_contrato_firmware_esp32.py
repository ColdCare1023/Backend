"""Contrato MQTT nodo edge → backend, verificado contra el payload literal que
emite el firmware ESP32.

`LecturaPayload` declara `extra="forbid"`, pero no contemplaba
`duracion_apertura_segundos`, un campo que `PayloadBuilder::build()` escribe en
TODAS las lecturas (0 con la puerta cerrada). El resultado era que el 100% de
las lecturas del firmware real se rechazaban con ValidationError: ninguna
prueba lo detectaba porque todas construían el payload a mano con los campos
que el backend ya esperaba.

Referencia del contrato: IoT-documentacion_iot.md §3.5 y
`iot-firmware/src/payload/PayloadBuilder.cpp`.
"""

import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from pydantic import ValidationError

from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.mqtt.payload_schema import LecturaPayload
from src.interface.api.sse_broadcaster import SSEBroadcaster
from src.interface.main import _procesar_mensaje_mqtt

DEVICE_ID = "FARM-01-CDL"


class _MensajeFalso:
    def __init__(self, topic: str, payload: str) -> None:
        self.topic = topic
        self.payload = payload.encode("utf-8")


class _BroadcasterEspia(SSEBroadcaster):
    def __init__(self) -> None:
        super().__init__()
        self.publicados: list[tuple[dict, str]] = []

    async def publicar(self, evento: dict, tipo: str = "lectura") -> None:  # type: ignore[override]
        self.publicados.append((evento, tipo))


def _payload_firmware(**overrides) -> str:
    """Réplica exacta de `PayloadBuilder::build()`: mismas claves, mismo orden,
    y `duracion_apertura_segundos` siempre presente."""
    base = {
        "device_id": DEVICE_ID,
        "timestamp": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "estado_conectividad": "online",
        "firmware_version": "1.0.0",
        "temperatura_interna": 4.5,
        "temperatura_ambiental": 5.2,
        "humedad_ambiental": 62.0,
        "apertura_refrigerador": False,
        "duracion_apertura_segundos": 0,
    }
    return json.dumps({**base, **overrides})


@pytest_asyncio.fixture
async def sesion_con_device(db_session_factory, monkeypatch):
    import src.interface.main as main

    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    async with db_session_factory() as session:
        await SQLAlchemyDeviceRepository(session).obtener_o_crear(DEVICE_ID)
        await session.commit()
    return db_session_factory


async def _lecturas(db_session_factory) -> list:
    async with db_session_factory() as session:
        return await SQLAlchemyLecturaRepository(session).listar(device_id=DEVICE_ID, limite=50)


def test_payload_literal_del_firmware_valida():
    """Regresión directa del defecto: antes lanzaba ValidationError
    ('extra_forbidden') sobre duracion_apertura_segundos."""
    payload = LecturaPayload.model_validate_json(_payload_firmware())

    assert payload.device_id == DEVICE_ID
    assert payload.firmware_version == "1.0.0"
    assert payload.duracion_apertura_segundos == 0


def test_apertura_con_duracion_conserva_el_valor():
    payload = LecturaPayload.model_validate_json(
        _payload_firmware(apertura_refrigerador=True, duracion_apertura_segundos=185)
    )

    assert payload.apertura_refrigerador is True
    assert payload.duracion_apertura_segundos == 185


def test_duracion_negativa_se_rechaza():
    """La duración es un contador monótono del nodo; un negativo indica
    corrupción del payload, no un caso válido."""
    with pytest.raises(ValidationError):
        LecturaPayload.model_validate_json(_payload_firmware(duracion_apertura_segundos=-1))


def test_campo_desconocido_sigue_rechazandose():
    """El contrato sigue cerrado: ampliarlo con un campo no debe convertirlo en
    permisivo."""
    with pytest.raises(ValidationError):
        LecturaPayload.model_validate_json(_payload_firmware(campo_inventado="x"))


@pytest.mark.asyncio
async def test_lectura_del_firmware_se_persiste_end_to_end(sesion_con_device):
    broadcaster = _BroadcasterEspia()
    mensaje = _MensajeFalso(f"farmacias/{DEVICE_ID}/lecturas", _payload_firmware())

    await _procesar_mensaje_mqtt(mensaje, broadcaster)

    lecturas = await _lecturas(sesion_con_device)
    assert len(lecturas) == 1
    assert lecturas[0].temperatura_interna == pytest.approx(4.5)
    # HU-17: primera lectura del dispositivo, sin historial para tendencia
    # térmica -> no_clasificable en vez de imputar una tendencia de 0.0.
    assert [tipo for _, tipo in broadcaster.publicados] == ["inferencia_omitida"]


@pytest.mark.asyncio
async def test_evidencia_edge_se_guarda_en_jsonb(sesion_con_device):
    """`firmware_version` y `duracion_apertura_segundos` no tienen columna
    propia en `thermal_readings`; se validaban y se descartaban. Ahora quedan
    en la columna JSONB `payload` (HU-22) para que la evidencia sea auditable."""
    broadcaster = _BroadcasterEspia()
    mensaje = _MensajeFalso(
        f"farmacias/{DEVICE_ID}/lecturas",
        _payload_firmware(apertura_refrigerador=True, duracion_apertura_segundos=240),
    )

    await _procesar_mensaje_mqtt(mensaje, broadcaster)

    lecturas = await _lecturas(sesion_con_device)
    assert lecturas[0].payload == {
        "firmware_version": "1.0.0",
        "duracion_apertura_segundos": 240,
    }
