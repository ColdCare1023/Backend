"""BE-015: las lecturas `simulated` solo se aceptan de dispositivos demo."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from src.infrastructure.database.models import AuditLogModel, DeviceModel, ThermalReadingModel
from src.interface import main

EJEMPLO = Path(__file__).resolve().parent.parent / "fixtures" / "contrato_v1" / "telemetria_demo_excursion_simulada.json"


class _Mensaje:
    def __init__(self, payload: dict) -> None:
        self.topic = f"farmacias/{payload['device_id']}/lecturas"
        self.payload = json.dumps(payload).encode()


class _Espia:
    def __init__(self) -> None:
        self.acuses: list[dict] = []

    async def publish(self, topic, payload=None, qos=0, **_kwargs):
        self.acuses.append(json.loads(payload))


class _Broadcaster:
    async def publicar(self, *_a, **_k):
        return None


def _simulada(device_id: str) -> dict:
    datos = json.loads(EJEMPLO.read_text(encoding="utf-8"))
    datos.update(device_id=device_id, captured_at=datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"))
    return datos


async def _alta(db_session_factory, device_id: str, es_demo: bool) -> None:
    async with db_session_factory() as session:
        session.add(DeviceModel(id=device_id, es_demo=es_demo))
        await session.commit()


@pytest.mark.asyncio
async def test_simulada_de_un_equipo_real_se_rechaza(db_session_factory, monkeypatch):
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    await _alta(db_session_factory, "ESP32_FARMACIA_REAL", es_demo=False)
    espia = _Espia()

    await main._procesar_lectura_mqtt(_Mensaje(_simulada("ESP32_FARMACIA_REAL")), _Broadcaster(), espia)

    assert espia.acuses[-1]["status"] == "rejected"
    async with db_session_factory() as session:
        assert (await session.execute(select(ThermalReadingModel))).first() is None
        acciones = (await session.execute(select(AuditLogModel.accion))).scalars().all()
    assert "LECTURA_SIMULADA_RECHAZADA" in acciones


@pytest.mark.asyncio
async def test_simulada_de_un_dispositivo_demo_se_acepta(db_session_factory, monkeypatch):
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    await _alta(db_session_factory, "ESP32_DEMO_01", es_demo=True)
    espia = _Espia()

    await main._procesar_lectura_mqtt(_Mensaje(_simulada("ESP32_DEMO_01")), _Broadcaster(), espia)

    assert espia.acuses[-1]["status"] == "committed"
