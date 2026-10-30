"""HU-44: revocar la credencial corta el aporte de evidencia del nodo en el
backend aunque el broker todavía lo deje conectar.

Con EMQX Serverless el usuario del broker se retira a mano en la consola
(`BROKER_USUARIOS_MODO=manual`), así que entre la revocación y ese retiro el nodo
comprometido puede reconectarse con el token anterior. El backend no persiste su
telemetría, audita cada intento y expulsa la sesión."""

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from src.infrastructure.config import get_settings
from src.infrastructure.database.models import AuditLogModel, ThermalReadingModel
from src.interface import main
from src.interface.api.dispositivos_router import get_broker_admin
from tests.conftest import auth_header

DEVICE_ID = "FARM-HU44APP"


class _Espia:
    configurado = True

    def __init__(self) -> None:
        self.acuses: list[dict] = []
        self.expulsados: list[str] = []
        self.orden: list[str] = []

    async def publish(self, topic, payload=None, qos=0, **_kwargs):
        self.acuses.append(json.loads(payload))
        self.orden.append("acuse")

    async def aprovisionar_credencial(self, device_id: str, token: str):
        return None  # modo manual: el usuario se crea a mano en la consola

    async def eliminar_credencial(self, device_id: str):
        return None  # modo manual: el usuario sigue vivo en el broker

    async def desconectar_cliente(self, client_id: str) -> bool:
        self.expulsados.append(client_id)
        self.orden.append("expulsion")
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
    get_settings.cache_clear()
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    doble = _Espia()
    monkeypatch.setattr(main, "BrokerAdmin", lambda _settings: doble)
    app.dependency_overrides[get_broker_admin] = lambda: doble
    yield doble
    get_settings.cache_clear()


def _alta_y_rotacion(client, token_admin) -> None:
    cabecera = auth_header(token_admin)
    assert client.post("/api/dispositivos", json={"device_id": DEVICE_ID}, headers=cabecera).status_code == 201
    assert client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar", json={"motivo": "alta"}, headers=cabecera
    ).status_code == 200


def _revocar(client, token_admin) -> None:
    respuesta = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/revocar",
        json={"motivo": "token expuesto"},
        headers=auth_header(token_admin),
    )
    assert respuesta.status_code == 204


def _telemetria(reading_id: str) -> dict:
    return {
        "device_id": DEVICE_ID, "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "temperatura_interna": 4.0, "temperatura_ambiental": 20.0, "humedad_ambiental": 50.0,
        "apertura_refrigerador": False, "estado_conectividad": "online", "reading_id": reading_id,
    }


async def _lecturas(db_session_factory) -> list:
    async with db_session_factory() as session:
        return (
            await session.execute(select(ThermalReadingModel).where(ThermalReadingModel.device_id == DEVICE_ID))
        ).all()


async def _acciones(db_session_factory) -> list[str]:
    async with db_session_factory() as session:
        return list((await session.execute(select(AuditLogModel.accion))).scalars().all())


async def test_credencial_vigente_acepta_la_lectura(client, token_admin, espia, db_session_factory):
    _alta_y_rotacion(client, token_admin)

    await main._procesar_lectura_mqtt(_Mensaje("lecturas", _telemetria("r1")), _Broadcaster(), espia)

    assert espia.acuses[-1]["estado"] != "dispositivo_no_autorizado"
    assert len(await _lecturas(db_session_factory)) == 1


async def test_lectura_con_credencial_revocada_no_se_persiste_y_se_audita(
    client, token_admin, espia, db_session_factory
):
    _alta_y_rotacion(client, token_admin)
    _revocar(client, token_admin)  # el usuario sigue en el broker (modo manual): el nodo aún puede publicar

    await main._procesar_lectura_mqtt(_Mensaje("lecturas", _telemetria("r2")), _Broadcaster(), espia)

    assert espia.acuses[-1]["estado"] == "dispositivo_no_autorizado"
    assert await _lecturas(db_session_factory) == []
    assert "LECTURA_RECHAZADA_CREDENCIAL_REVOCADA" in await _acciones(db_session_factory)
    assert espia.expulsados[-1] == DEVICE_ID


async def test_revocada_publica_el_acuse_antes_de_expulsar_y_expulsa_una_vez(
    client, token_admin, espia, db_session_factory
):
    _alta_y_rotacion(client, token_admin)
    _revocar(client, token_admin)
    espia.orden.clear()

    for n in range(3):
        await main._procesar_lectura_mqtt(_Mensaje("lecturas", _telemetria(f"r{n}")), _Broadcaster(), espia)

    # El acuse sale antes de la expulsión (que se lo llevaría) y en cada intento;
    # la expulsión, una sola vez por ventana.
    assert espia.orden == ["acuse", "expulsion", "acuse", "acuse"]
    filas = [a for a in await _acciones(db_session_factory) if a == "LECTURA_RECHAZADA_CREDENCIAL_REVOCADA"]
    assert len(filas) == 1  # tope de auditoría: el resto solo se cuenta


async def test_evento_con_credencial_revocada_no_altera_el_estado(client, token_admin, espia, db_session_factory):
    _alta_y_rotacion(client, token_admin)
    _revocar(client, token_admin)

    evento = {"device_id": DEVICE_ID, "tipo_evento": "lwt_online", "timestamp": datetime.now(tz=timezone.utc).isoformat()}
    await main._procesar_evento_mqtt(_Mensaje("eventos", evento), _Broadcaster())

    acciones = await _acciones(db_session_factory)
    assert "EVENTO_CREDENCIAL_REVOCADA" in acciones
    assert "DISPOSITIVO_ONLINE" not in acciones


async def test_rotar_despues_de_revocar_restablece_la_ingesta(client, token_admin, espia, db_session_factory):
    _alta_y_rotacion(client, token_admin)
    _revocar(client, token_admin)
    assert client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar", json={"motivo": "reemplazo"}, headers=auth_header(token_admin)
    ).status_code == 200

    await main._procesar_lectura_mqtt(_Mensaje("lecturas", _telemetria("r3")), _Broadcaster(), espia)

    assert espia.acuses[-1]["estado"] != "dispositivo_no_autorizado"
    assert len(await _lecturas(db_session_factory)) == 1
