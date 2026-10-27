"""HU-43/HU-44: el alta emite la credencial y lo que el broker no confirmó queda
pendiente, visible y auditado hasta que el administrador confirma la consola."""

import pytest
from sqlalchemy import select

from src.infrastructure.config import get_settings
from src.infrastructure.database.models import AuditLogModel, TraceabilityRecordModel
from src.interface.api.dispositivos_router import get_broker_admin
from tests.conftest import auth_header

DEVICE_ID = "FARM-PEND-01"


class _BrokerManual:
    """EMQX Serverless: la API solo expulsa sesiones; usuarios a mano."""

    configurado = True

    def __init__(self, resultado: bool | None = None) -> None:
        self.resultado = resultado

    async def aprovisionar_credencial(self, device_id: str, token: str):
        return self.resultado

    async def eliminar_credencial(self, device_id: str):
        return self.resultado

    async def desconectar_cliente(self, client_id: str) -> bool:
        return True


@pytest.fixture
def broker(app):
    get_settings.cache_clear()
    doble = _BrokerManual()
    app.dependency_overrides[get_broker_admin] = lambda: doble
    yield doble
    get_settings.cache_clear()


def _alta(client, token_admin):
    return client.post("/api/dispositivos", json={"device_id": DEVICE_ID}, headers=auth_header(token_admin))


def _dispositivo(client, token_admin) -> dict:
    lista = client.get("/api/dispositivos", headers=auth_header(token_admin)).json()
    return next(d for d in lista if d["id"] == DEVICE_ID)


async def _acciones(db_session_factory) -> list[str]:
    async with db_session_factory() as session:
        return list((await session.execute(select(AuditLogModel.accion))).scalars().all())


def test_el_alta_emite_la_credencial_una_sola_vez(client, token_admin, broker):
    respuesta = _alta(client, token_admin)

    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert len(cuerpo["credencial"]["token"]) >= 32
    assert cuerpo["mqtt_token_activo"] is True
    assert "token" not in _dispositivo(client, token_admin)


async def test_alta_en_modo_manual_deja_el_aprovisionamiento_pendiente(
    client, token_admin, broker, db_session_factory
):
    cuerpo = _alta(client, token_admin).json()

    assert cuerpo["credencial"]["credencial_aprovisionada"] is None
    assert "Pendiente en el broker" in cuerpo["credencial"]["mensaje_broker"]
    assert cuerpo["broker_pendiente"] == "aprovisionar"
    assert cuerpo["broker_pendiente_motivo"] == "modo_manual"
    assert cuerpo["broker_pendiente_desde"] is not None
    assert cuerpo["broker_pendiente_por"] is not None
    assert "BROKER_OPERACION_PENDIENTE" in await _acciones(db_session_factory)
    async with db_session_factory() as session:
        eventos = (await session.execute(select(TraceabilityRecordModel.tipo_evento))).scalars().all()
    assert "EMISION_CREDENCIAL_DISPOSITIVO" in eventos
    assert "BROKER_OPERACION_PENDIENTE" in eventos


def test_revocar_en_modo_manual_deja_pendiente_retirar(client, token_admin, broker):
    _alta(client, token_admin)
    assert client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/revocar", json={"motivo": "expuesto"},
        headers=auth_header(token_admin),
    ).status_code == 204

    assert _dispositivo(client, token_admin)["broker_pendiente"] == "retirar"


def test_error_de_la_api_del_broker_tambien_queda_pendiente(client, token_admin, broker):
    broker.resultado = False
    cuerpo = _alta(client, token_admin).json()

    assert cuerpo["broker_pendiente"] == "aprovisionar"
    assert cuerpo["broker_pendiente_motivo"] == "api_broker_error"
    assert "no aceptó" in cuerpo["credencial"]["mensaje_broker"]


async def test_confirmar_en_consola_limpia_el_pendiente_y_lo_audita(
    client, token_admin, broker, db_session_factory
):
    _alta(client, token_admin)

    respuesta = client.post(
        f"/api/dispositivos/{DEVICE_ID}/broker/confirmar", json={"nota": "usuario creado en la consola"},
        headers=auth_header(token_admin),
    )

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["broker_pendiente"] is None
    assert cuerpo["broker_confirmado_en"] is not None
    assert cuerpo["broker_confirmado_por"] is not None
    assert "BROKER_CONSOLA_CONFIRMADA" in await _acciones(db_session_factory)


def test_confirmar_sin_pendiente_es_conflicto(client, token_admin, broker):
    broker.resultado = True  # modo api: el broker confirmó, nada pendiente
    assert _alta(client, token_admin).json()["broker_pendiente"] is None

    respuesta = client.post(
        f"/api/dispositivos/{DEVICE_ID}/broker/confirmar", json={}, headers=auth_header(token_admin)
    )
    assert respuesta.status_code == 409


def test_aprovisionar_por_api_limpia_un_pendiente_anterior(client, token_admin, broker):
    _alta(client, token_admin)  # queda pendiente (modo manual)
    broker.resultado = True
    assert client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar", json={"motivo": "reintento"},
        headers=auth_header(token_admin),
    ).status_code == 200

    assert _dispositivo(client, token_admin)["broker_pendiente"] is None


def test_solo_el_administrador_confirma(client, token_admin, token_tecnico, broker):
    _alta(client, token_admin)
    respuesta = client.post(
        f"/api/dispositivos/{DEVICE_ID}/broker/confirmar", json={}, headers=auth_header(token_tecnico)
    )
    assert respuesta.status_code == 403
