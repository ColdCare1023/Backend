"""BE-019: consumo de recursos acotado.

- La verificación global de la cadena recorre los registros en streaming.
- Los eventos MQTT tienen techo de tamaño ANTES de deserializar y su `detail`
  está acotado (termina en audit_logs y en la cadena).
- Un usuario no abre streams SSE ilimitados.
- Las operaciones costosas (verificar la cadena, generar reportes) tienen un
  tope de ejecuciones simultáneas en el proceso.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import select

from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.application.use_cases.verificar_integridad_registro import VerificarIntegridadRegistroUseCase
from src.infrastructure.config import get_settings
from src.infrastructure.database.models import AuditLogModel
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.trazabilidad_repository import SQLAlchemyTrazabilidadRepository
from src.infrastructure.mqtt import contrato_v1
from src.infrastructure.mqtt.payload_schema import MAX_BYTES_PAYLOAD_MQTT
from src.infrastructure.security.jwt_handler import JWTHandler
from src.interface import main
from src.interface.api import sse_router
from tests.conftest import auth_header

EJEMPLOS = Path(__file__).resolve().parent.parent / "fixtures" / "contrato_v1"
DEV = "ESP32_FARMACIA_01"


class _Mensaje:
    def __init__(self, topic: str, payload: str) -> None:
        self.topic = topic
        self.payload = payload.encode("utf-8")


class _Mudo:
    def __init__(self) -> None:
        self.eventos: list = []

    async def publicar(self, datos, tipo) -> None:
        self.eventos.append((datos, tipo))


@pytest_asyncio.fixture
async def entorno(db_session_factory, monkeypatch):
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    async with db_session_factory() as session:
        await SQLAlchemyDeviceRepository(session).obtener_o_crear(DEV)
        await session.commit()
    return db_session_factory


def _evento(detalle: dict | None) -> dict:
    datos = json.loads((EJEMPLOS / "evento_buffer_saturado.json").read_text(encoding="utf-8"))
    datos["detail"] = detalle
    return datos


async def _auditados(fabrica) -> list:
    async with fabrica() as session:
        return (await session.execute(select(AuditLogModel))).scalars().all()


# ── Verificación en streaming ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_la_verificacion_global_no_materializa_la_cadena(db_session, monkeypatch):
    repo = SQLAlchemyTrazabilidadRepository(db_session)
    registrar = RegistrarHashEncadenadoUseCase(repo)
    for indice in range(12):
        await registrar.execute("EVENTO", {"i": indice}, device_id="D-STREAM")
    await db_session.commit()

    async def _prohibido(*_args, **_kwargs):
        raise AssertionError("la verificación global no debe cargar toda la cadena en memoria")

    monkeypatch.setattr(repo, "listar_todos_ordenados", _prohibido)

    resultado = await VerificarIntegridadRegistroUseCase(repo).execute()

    assert resultado.total_registros == 12
    assert resultado.integra is True


# ── Eventos MQTT ──────────────────────────────────────────────────────────


def test_el_detalle_de_un_evento_admite_un_tamano_normal():
    evento = contrato_v1.EventoV1.model_validate(_evento({"pendientes": 120, "descartadas": 3}))
    assert evento.detail == {"pendientes": 120, "descartadas": 3}


@pytest.mark.parametrize(
    "detalle",
    [
        {f"k{i}": i for i in range(contrato_v1.MAX_CLAVES_DETALLE + 1)},
        {"texto": "x" * (contrato_v1.MAX_BYTES_DETALLE + 1)},
    ],
    ids=["demasiadas_claves", "demasiados_bytes"],
)
def test_el_detalle_de_un_evento_esta_acotado(detalle):
    with pytest.raises(ValidationError):
        contrato_v1.EventoV1.model_validate(_evento(detalle))


@pytest.mark.asyncio
async def test_un_evento_con_detalle_inflado_no_se_audita(entorno):
    mudo = _Mudo()
    datos = _evento({"texto": "x" * 2000})
    assert len(json.dumps(datos)) < MAX_BYTES_PAYLOAD_MQTT

    await main._procesar_evento_mqtt(_Mensaje(f"farmacias/{DEV}/eventos", json.dumps(datos)), mudo)

    assert await _auditados(entorno) == []
    assert mudo.eventos == []


@pytest.mark.asyncio
async def test_un_evento_que_excede_el_techo_se_descarta_sin_deserializar(entorno, monkeypatch):
    def _no_deserializar(*_args, **_kwargs):
        raise AssertionError("un payload sobredimensionado no debe llegar a json.loads")

    mudo = _Mudo()
    cuerpo = json.dumps(_evento(None)) + " " * MAX_BYTES_PAYLOAD_MQTT
    monkeypatch.setattr(main.json, "loads", _no_deserializar)

    await main._procesar_evento_mqtt(_Mensaje(f"farmacias/{DEV}/eventos", cuerpo), mudo)

    monkeypatch.undo()
    assert await _auditados(entorno) == []
    assert mudo.eventos == []


@pytest.mark.asyncio
async def test_un_evento_valido_se_sigue_auditando(entorno):
    mudo = _Mudo()
    await main._procesar_evento_mqtt(_Mensaje(f"farmacias/{DEV}/eventos", json.dumps(_evento(None))), mudo)

    assert {a.accion for a in await _auditados(entorno)} == {"BUFFER_SATURADO"}


# ── Streams SSE por usuario ───────────────────────────────────────────────


def _ticket(client, token) -> tuple[str, object]:
    ticket = client.post("/api/auth/sse-ticket", headers=auth_header(token)).json()["ticket"]
    return ticket, JWTHandler(get_settings()).validar_ticket_sse(ticket).usuario_id


def test_supera_el_maximo_de_streams_recibe_429(client, app, token_tecnico):
    ticket, usuario = _ticket(client, token_tecnico)
    app.state.sse_streams_por_usuario[usuario] = get_settings().sse_max_streams_por_usuario

    respuesta = client.get("/api/sse/lecturas", params={"ticket": ticket})

    assert respuesta.status_code == 429
    assert int(respuesta.headers["Retry-After"]) > 0


def test_el_limite_es_por_usuario(client, app, token_tecnico, token_farmaceutico, monkeypatch):
    _ticket_tecnico, tecnico = _ticket(client, token_tecnico)
    app.state.sse_streams_por_usuario[tecnico] = get_settings().sse_max_streams_por_usuario
    ticket, farmaceutico = _ticket(client, token_farmaceutico)
    llamadas = {"n": 0}

    async def _vigente_solo_al_abrir(_fabrica, _ticket):
        llamadas["n"] += 1
        return llamadas["n"] == 1

    monkeypatch.setattr(sse_router, "sesion_del_ticket_vigente", _vigente_solo_al_abrir)

    respuesta = client.get("/api/sse/lecturas", params={"ticket": ticket})

    assert respuesta.status_code == 200
    assert "sesion_expirada" in respuesta.text
    assert farmaceutico not in app.state.sse_streams_por_usuario


def test_al_cerrar_el_stream_se_libera_el_cupo(client, app, token_tecnico, monkeypatch):
    ticket, usuario = _ticket(client, token_tecnico)
    maximo = get_settings().sse_max_streams_por_usuario
    app.state.sse_streams_por_usuario[usuario] = maximo - 1
    llamadas = {"n": 0}

    async def _vigente_solo_al_abrir(_fabrica, _ticket):
        llamadas["n"] += 1
        return llamadas["n"] == 1

    monkeypatch.setattr(sse_router, "sesion_del_ticket_vigente", _vigente_solo_al_abrir)

    respuesta = client.get("/api/sse/lecturas", params={"ticket": ticket})

    assert respuesta.status_code == 200
    # Ni fuga (quedaría en `maximo`) ni doble liberación (quedaría en maximo - 2).
    assert app.state.sse_streams_por_usuario[usuario] == maximo - 1


# ── Operaciones costosas concurrentes ────────────────────────────────────


@pytest.mark.parametrize(
    ("ruta", "semaforo", "params"),
    [
        ("/api/trazabilidad/verificar", "verificacion", {}),
        ("/api/trazabilidad/verificar-dispositivo", "verificacion", "dispositivo"),
        ("/api/reportes/bpa", "reportes", "rango"),
        ("/api/reportes/bpa/pdf", "reportes", "rango"),
    ],
)
def test_operacion_costosa_saturada_responde_503(client, app, token_farmaceutico, ruta, semaforo, params):
    ahora = datetime.now(tz=timezone.utc)
    desde, hasta = (ahora - timedelta(days=1)).isoformat(), (ahora + timedelta(days=1)).isoformat()
    if params == "rango":
        params = {"fecha_desde": desde, "fecha_hasta": hasta}
    elif params == "dispositivo":
        params = {"device_id": DEV, "desde": desde, "hasta": hasta}
    # Semáforo agotado: todas las plazas ocupadas por otras peticiones.
    app.state.semaforos_costosos[semaforo] = asyncio.Semaphore(0)

    respuesta = client.get(ruta, params=params, headers=auth_header(token_farmaceutico))

    assert respuesta.status_code == 503
    assert int(respuesta.headers["Retry-After"]) > 0


def test_las_plazas_se_devuelven_al_terminar(client, token_farmaceutico):
    maximo = get_settings().operaciones_costosas_concurrentes
    for _ in range(maximo + 2):
        respuesta = client.get("/api/trazabilidad/verificar", headers=auth_header(token_farmaceutico))
        assert respuesta.status_code == 200
