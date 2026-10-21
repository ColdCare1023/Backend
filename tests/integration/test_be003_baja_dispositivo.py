"""BE-003: un dispositivo dado de baja pierde su credencial y su capacidad de
aportar evidencia (lecturas y eventos), aunque siga provisionado en `devices`."""

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from src.infrastructure.config import get_settings
from src.infrastructure.database.models import AuditLogModel, DeviceModel, ThermalReadingModel
from src.interface import main
from src.interface.api.dispositivos_router import get_broker_admin
from tests.conftest import auth_header

DEVICE_ID = "FARM-BE003"
SECRETO = "secreto-del-broker-para-pruebas-0123456789"


class _Espia:
    def __init__(self) -> None:
        self.acuses: list[dict] = []
        self.expulsados: list[str] = []

    async def publish(self, topic, payload=None, qos=0, **_kwargs):
        self.acuses.append(json.loads(payload))

    async def desconectar_cliente(self, client_id: str) -> bool:
        self.expulsados.append(client_id)
        return True


class _Broadcaster:
    async def publicar(self, *_a, **_k):
        return None


class _Mensaje:
    def __init__(self, sufijo: str, payload: dict) -> None:
        self.topic = f"farmacias/{DEVICE_ID}/{sufijo}"
        self.payload = json.dumps(payload).encode()


@pytest.fixture
def espia(app, monkeypatch, db_session_factory):
    monkeypatch.setenv("BROKER_WEBHOOK_SECRET", SECRETO)
    get_settings.cache_clear()
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    doble = _Espia()
    app.dependency_overrides[get_broker_admin] = lambda: doble
    yield doble
    get_settings.cache_clear()


def _alta_con_credencial(client, token_admin) -> str:
    assert client.post("/api/dispositivos", json={"device_id": DEVICE_ID}, headers=auth_header(token_admin)).status_code == 201
    return client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar", json={"motivo": "alta"}, headers=auth_header(token_admin)
    ).json()["token"]


def _baja(client, token_admin) -> None:
    respuesta = client.post(
        f"/api/dispositivos/{DEVICE_ID}/baja", json={"motivo": "falla_hardware"}, headers=auth_header(token_admin)
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["mqtt_token_activo"] is False


async def test_baja_revoca_credencial_y_expulsa_la_sesion(client, token_admin, espia, db_session_factory):
    token = _alta_con_credencial(client, token_admin)
    _baja(client, token_admin)

    autenticacion = client.post(
        "/api/dispositivos/autenticar", json={"device_id": DEVICE_ID, "token": token},
        headers={"X-Broker-Auth": SECRETO},
    )
    assert autenticacion.json()["result"] == "deny"
    assert espia.expulsados[-1] == DEVICE_ID
    async with db_session_factory() as session:
        assert (await session.get(DeviceModel, DEVICE_ID)).mqtt_token_activo is False


async def test_lecturas_de_un_dispositivo_dado_de_baja_se_rechazan(client, token_admin, espia, db_session_factory):
    _alta_con_credencial(client, token_admin)
    _baja(client, token_admin)

    telemetria = {
        "device_id": DEVICE_ID, "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "temperatura_interna": 4.0, "temperatura_ambiental": 20.0, "humedad_ambiental": 50.0,
        "apertura_refrigerador": False, "estado_conectividad": "online", "reading_id": "r1",
    }
    await main._procesar_lectura_mqtt(_Mensaje("lecturas", telemetria), _Broadcaster(), espia)

    assert espia.acuses[-1]["estado"] == "dispositivo_no_autorizado"
    async with db_session_factory() as session:
        filas = (await session.execute(select(ThermalReadingModel).where(ThermalReadingModel.device_id == DEVICE_ID))).all()
    assert filas == []


async def test_eventos_de_un_dispositivo_dado_de_baja_no_cambian_su_estado(client, token_admin, espia, db_session_factory):
    _alta_con_credencial(client, token_admin)
    _baja(client, token_admin)

    evento = {"device_id": DEVICE_ID, "tipo_evento": "lwt_online", "timestamp": datetime.now(tz=timezone.utc).isoformat()}
    await main._procesar_evento_mqtt(_Mensaje("eventos", evento), _Broadcaster())

    async with db_session_factory() as session:
        acciones = (await session.execute(select(AuditLogModel.accion))).scalars().all()
    assert "EVENTO_DISPOSITIVO_DADO_DE_BAJA" in acciones
    assert "DISPOSITIVO_ONLINE" not in acciones
