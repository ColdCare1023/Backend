"""HU-43 esc. 4 / HU-44 esc. 3 / HU-10: el resultado de la sincronización con el
broker queda en la bitácora también cuando el broker SÍ la confirmó.

Antes solo se auditaba lo que quedaba pendiente (BROKER_OPERACION_PENDIENTE).
Con la API del broker (EMQX 5 completo o Dedicated), el aprovisionamiento, el
retiro del usuario y la expulsión de la sesión no dejaban ningún evento: la
evidencia del «resultado de la operación en EMQX» que piden los criterios
no existía. Ahora cada llamada que el broker confirma deja
BROKER_OPERACION_SINCRONIZADA (append-only, encadenada en la cadena del
dispositivo, como los demás eventos del broker) y una expulsión que el broker
no hizo deja BROKER_OPERACION_FALLIDA."""

import pytest
from sqlalchemy import select

from src.infrastructure.config import get_settings
from src.infrastructure.database.models import AuditLogModel, TraceabilityRecordModel, UserModel
from src.interface.api.dispositivos_router import get_broker_admin
from tests.conftest import auth_header

DEVICE_ID = "FARM-SYNC-01"


class _BrokerApi:
    """EMQX 5 completo/Dedicated: el backend gestiona usuarios y sesiones por API."""

    configurado = True
    gestiona_usuarios = True

    def __init__(self) -> None:
        self.credencial: bool | None = True
        self.expulsion: bool | None = True

    async def aprovisionar_credencial(self, device_id: str, token: str):
        return self.credencial

    async def eliminar_credencial(self, device_id: str):
        return self.credencial

    async def desconectar_cliente(self, client_id: str):
        return self.expulsion


@pytest.fixture
def broker(app):
    get_settings.cache_clear()
    doble = _BrokerApi()
    app.dependency_overrides[get_broker_admin] = lambda: doble
    yield doble
    get_settings.cache_clear()


def _alta(client, token_admin):
    respuesta = client.post("/api/dispositivos", json={"device_id": DEVICE_ID}, headers=auth_header(token_admin))
    assert respuesta.status_code == 201
    return respuesta.json()


async def _eventos(db_session_factory, accion: str) -> list[AuditLogModel]:
    async with db_session_factory() as session:
        return list(
            (
                await session.execute(
                    select(AuditLogModel).where(AuditLogModel.accion == accion).order_by(AuditLogModel.created_at)
                )
            ).scalars().all()
        )


async def _operaciones(db_session_factory, accion: str) -> list[str]:
    return [e.detalle["operacion"] for e in await _eventos(db_session_factory, accion)]


async def _eslabones(db_session_factory, tipo: str) -> list[TraceabilityRecordModel]:
    async with db_session_factory() as session:
        return list(
            (
                await session.execute(
                    select(TraceabilityRecordModel)
                    .where(TraceabilityRecordModel.tipo_evento == tipo)
                    .order_by(TraceabilityRecordModel.chain_seq)
                )
            ).scalars().all()
        )


async def test_alta_aprovisionada_por_api_queda_auditada_y_encadenada(
    client, token_admin, broker, db_session_factory
):
    cuerpo = _alta(client, token_admin)

    assert cuerpo["credencial"]["credencial_aprovisionada"] is True
    assert cuerpo["broker_pendiente"] is None
    eventos = await _eventos(db_session_factory, "BROKER_OPERACION_SINCRONIZADA")
    assert [e.detalle for e in eventos] == [{"operacion": "aprovisionar", "origen": "alta"}]
    evento = eventos[0]
    assert evento.resultado == "exitoso"
    assert evento.recurso == f"dispositivos/{DEVICE_ID}/broker"
    async with db_session_factory() as session:
        admin = (await session.execute(select(UserModel).where(UserModel.email == "admin@farmacia.example.org"))).scalar_one()
    assert evento.usuario_id == admin.id
    assert await _operaciones(db_session_factory, "BROKER_OPERACION_PENDIENTE") == []

    # Eslabón propio en la cadena del dispositivo, como BROKER_OPERACION_PENDIENTE.
    eslabones = await _eslabones(db_session_factory, "BROKER_OPERACION_SINCRONIZADA")
    assert [(e.chain_id, e.payload["operacion"]) for e in eslabones] == [(DEVICE_ID, "aprovisionar")]
    assert eslabones[0].payload["actor_id"] == str(admin.id)
    # Y la fila de la bitácora también va encadenada en la cadena del dispositivo.
    auditadas = [
        e for e in await _eslabones(db_session_factory, "AUDIT_LOG")
        if e.payload["accion"] == "BROKER_OPERACION_SINCRONIZADA"
    ]
    assert [e.chain_id for e in auditadas] == [DEVICE_ID]


async def test_baja_por_api_audita_el_retiro_y_la_expulsion(client, token_admin, broker, db_session_factory):
    _alta(client, token_admin)

    respuesta = client.post(
        f"/api/dispositivos/{DEVICE_ID}/baja", json={"motivo": "falla_hardware", "descripcion": "sensor dañado"},
        headers=auth_header(token_admin),
    )

    assert respuesta.status_code == 200
    eventos = await _eventos(db_session_factory, "BROKER_OPERACION_SINCRONIZADA")
    assert [e.detalle for e in eventos[1:]] == [
        {"operacion": "retirar", "origen": "baja", "motivo": "falla_hardware"},
        {"operacion": "expulsar_sesion", "origen": "baja", "motivo": "falla_hardware"},
    ]
    assert {e.resultado for e in eventos} == {"exitoso"}


async def test_rotacion_por_api_espera_el_relevo_y_lo_carga_al_confirmar(
    client, token_admin, broker, db_session_factory
):
    """HU-44 esc. 2: el token nuevo va PRIMERO al nodo (`mqtt_relevo`) y después al
    broker. Con el broker por API la rotación no lo carga ni expulsa la sesión:
    queda «esperando_relevo» hasta que el administrador confirma con el token."""
    _alta(client, token_admin)
    cabecera = auth_header(token_admin)

    respuesta = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar", json={"motivo": "programada"}, headers=cabecera,
    )

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    token = cuerpo["token"]
    assert cuerpo["sesion_broker_cerrada"] is None and cuerpo["credencial_aprovisionada"] is None
    assert "mqtt_relevo" in cuerpo["mensaje_broker"]
    # Solo el aprovisionamiento del alta: la rotación todavía no tocó el broker.
    assert await _operaciones(db_session_factory, "BROKER_OPERACION_SINCRONIZADA") == ["aprovisionar"]
    dispositivo = next(d for d in client.get("/api/dispositivos", headers=cabecera).json() if d["id"] == DEVICE_ID)
    assert dispositivo["broker_pendiente"] == "aprovisionar"
    assert dispositivo["broker_pendiente_motivo"] == "esperando_relevo"

    url = f"/api/dispositivos/{DEVICE_ID}/broker/confirmar"
    assert client.post(url, json={}, headers=cabecera).status_code == 422  # falta el token
    assert client.post(url, json={"token": "x" * 43}, headers=cabecera).status_code == 422  # no es el emitido
    confirmada = client.post(url, json={"token": token, "nota": "relevo cargado"}, headers=cabecera)

    assert confirmada.status_code == 200, confirmada.text
    assert confirmada.json()["broker_pendiente"] is None
    assert await _operaciones(db_session_factory, "BROKER_OPERACION_SINCRONIZADA") == [
        "aprovisionar", "aprovisionar", "expulsar_sesion",
    ]
    relevo = (await _eventos(db_session_factory, "BROKER_OPERACION_SINCRONIZADA"))[1]
    assert relevo.detalle == {"operacion": "aprovisionar", "origen": "relevo_confirmado"}


async def test_relevo_rechazado_por_la_api_sigue_pendiente(client, token_admin, broker, db_session_factory):
    _alta(client, token_admin)
    cabecera = auth_header(token_admin)
    token = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar", json={"motivo": "programada"}, headers=cabecera,
    ).json()["token"]
    broker.credencial = False

    respuesta = client.post(f"/api/dispositivos/{DEVICE_ID}/broker/confirmar", json={"token": token}, headers=cabecera)

    assert respuesta.status_code == 502
    dispositivo = next(d for d in client.get("/api/dispositivos", headers=cabecera).json() if d["id"] == DEVICE_ID)
    assert dispositivo["broker_pendiente"] == "aprovisionar"


async def test_revocacion_por_api_audita_retiro_y_expulsion(client, token_admin, broker, db_session_factory):
    _alta(client, token_admin)

    assert client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/revocar", json={"motivo": "expuesto"},
        headers=auth_header(token_admin),
    ).status_code == 204

    assert await _operaciones(db_session_factory, "BROKER_OPERACION_SINCRONIZADA") == [
        "aprovisionar", "retirar", "expulsar_sesion",
    ]
    eslabones = await _eslabones(db_session_factory, "BROKER_OPERACION_SINCRONIZADA")
    assert [e.payload["operacion"] for e in eslabones] == ["aprovisionar", "retirar", "expulsar_sesion"]
    assert {e.chain_id for e in eslabones} == {DEVICE_ID}


async def test_en_modo_manual_la_expulsion_por_api_tambien_se_audita(
    client, token_admin, broker, db_session_factory
):
    """EMQX Serverless: el usuario se retira a mano (pendiente), pero la API sí
    expulsó la sesión; las dos cosas quedan en la bitácora."""
    broker.credencial = None
    _alta(client, token_admin)

    client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/revocar", json={"motivo": "expuesto"},
        headers=auth_header(token_admin),
    )

    assert await _operaciones(db_session_factory, "BROKER_OPERACION_PENDIENTE") == ["aprovisionar", "retirar"]
    assert await _operaciones(db_session_factory, "BROKER_OPERACION_SINCRONIZADA") == ["expulsar_sesion"]


async def test_expulsion_que_el_broker_no_hizo_queda_como_fallida(
    client, token_admin, broker, db_session_factory
):
    """HU-44 esc. 3: el sistema no informa éxito en silencio."""
    _alta(client, token_admin)
    broker.expulsion = False

    client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/revocar", json={"motivo": "expuesto"},
        headers=auth_header(token_admin),
    )

    fallidas = await _eventos(db_session_factory, "BROKER_OPERACION_FALLIDA")
    assert [(e.detalle, e.resultado) for e in fallidas] == [
        ({"operacion": "expulsar_sesion", "origen": "revocacion", "motivo": "expuesto"}, "fallido")
    ]
    assert [e.chain_id for e in await _eslabones(db_session_factory, "BROKER_OPERACION_FALLIDA")] == [DEVICE_ID]
    assert "expulsar_sesion" not in await _operaciones(db_session_factory, "BROKER_OPERACION_SINCRONIZADA")


async def test_sin_api_del_broker_no_se_inventa_ningun_resultado(
    client, token_admin, broker, db_session_factory
):
    """Sin BROKER_API_URL no se intentó nada: queda el pendiente, sin éxito ni fallo."""
    broker.credencial = None
    broker.expulsion = None
    _alta(client, token_admin)

    client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/revocar", json={"motivo": "expuesto"},
        headers=auth_header(token_admin),
    )

    assert await _operaciones(db_session_factory, "BROKER_OPERACION_SINCRONIZADA") == []
    assert await _operaciones(db_session_factory, "BROKER_OPERACION_FALLIDA") == []
    assert await _operaciones(db_session_factory, "BROKER_OPERACION_PENDIENTE") == ["aprovisionar", "retirar"]


async def test_confirmar_la_consola_audita_la_expulsion_posterior(
    client, token_admin, broker, db_session_factory
):
    broker.credencial = None
    _alta(client, token_admin)

    assert client.post(
        f"/api/dispositivos/{DEVICE_ID}/broker/confirmar", json={"nota": "usuario creado"},
        headers=auth_header(token_admin),
    ).status_code == 200

    eventos = await _eventos(db_session_factory, "BROKER_OPERACION_SINCRONIZADA")
    assert [e.detalle for e in eventos] == [{"operacion": "expulsar_sesion", "origen": "consola_confirmada"}]


def test_el_tecnico_no_puede_disparar_operaciones_del_broker(client, token_admin, token_tecnico, broker):
    """RBAC: las operaciones que sincronizan con el broker son solo de ADMINISTRADOR."""
    _alta(client, token_admin)

    respuesta = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/revocar", json={"motivo": "x"},
        headers=auth_header(token_tecnico),
    )

    assert respuesta.status_code == 403
