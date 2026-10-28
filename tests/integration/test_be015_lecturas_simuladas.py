"""BE-015: las lecturas `simulated` solo se aceptan de dispositivos demo."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from src.infrastructure.config import Settings, get_settings
from src.infrastructure.database.models import AuditLogModel, DeviceModel, ThermalReadingModel
from src.interface import main
from tests.conftest import auth_header

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


def test_en_produccion_la_api_no_da_de_alta_un_dispositivo_demo(client, token_admin):
    """El frontend no expone es_demo; en producción tampoco lo admite la API:
    un dispositivo demo en el piloto aceptaría lecturas simuladas."""
    produccion = Settings(
        environment="production", jwt_secret_key=get_settings().jwt_secret_key, trazabilidad_hmac_key="h" * 40,
        allowed_hosts=["testserver"], cors_origins=["https://app.example.org"],
        database_url="postgresql+asyncpg://u:p@db/prod", mqtt_enabled=False,
    )
    client.app.dependency_overrides[get_settings] = lambda: produccion
    try:
        demo = client.post(
            "/api/dispositivos", json={"device_id": "ESP32_BANCO_01", "es_demo": True}, headers=auth_header(token_admin)
        )
        real = client.post("/api/dispositivos", json={"device_id": "ESP32_FARMACIA_02"}, headers=auth_header(token_admin))
    finally:
        client.app.dependency_overrides.pop(get_settings)

    assert demo.status_code == 422
    assert "producción" in demo.json()["detail"]
    assert real.status_code == 201


def test_fuera_de_produccion_se_admite_el_dispositivo_de_banco(client, token_admin):
    respuesta = client.post(
        "/api/dispositivos", json={"device_id": "ESP32_BANCO_01", "es_demo": True}, headers=auth_header(token_admin)
    )
    assert respuesta.status_code == 201
    assert respuesta.json()["es_demo"] is True
