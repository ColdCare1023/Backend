"""HU-13: estado de conectividad del dispositivo (tabla 5.3 de la especificación).

Los eventos entran por el manejador MQTT real (`_procesar_evento_mqtt`) con el
contrato v1.0 del firmware ColdCare."""

import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.infrastructure.database.models import AuditLogModel, DeviceModel
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.interface import main

DEV = "ESP32_HU13_01"


class _Mensaje:
    def __init__(self, topic: str, payload: str) -> None:
        self.topic = topic
        self.payload = payload.encode("utf-8")


class _Espia:
    """Registra los eventos SSE y, para `conectividad`, comprueba que el
    estado ya está confirmado en la BD cuando se publica (D2)."""

    def __init__(self, factory) -> None:
        self.eventos: list[tuple[str, dict]] = []
        self.estado_confirmado_al_publicar: list[str] = []
        self._factory = factory

    async def publicar(self, evento, tipo="lectura"):
        self.eventos.append((tipo, evento))
        if tipo == "conectividad":
            async with self._factory() as session:
                self.estado_confirmado_al_publicar.append(
                    (await session.get(DeviceModel, DEV)).estado_conectividad
                )

    def de_tipo(self, tipo: str) -> list[dict]:
        return [e for t, e in self.eventos if t == tipo]


@pytest_asyncio.fixture
async def entorno(db_session_factory, monkeypatch):
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    async with db_session_factory() as session:
        await SQLAlchemyDeviceRepository(session).obtener_o_crear(DEV)
        await session.commit()
    return db_session_factory


def _evento(event_type: str, boot_id: int | None = None) -> _Mensaje:
    cuerpo: dict = {"schema_version": "1.0", "device_id": DEV, "event_type": event_type}
    if boot_id is not None:
        cuerpo["boot_id"] = boot_id
    if event_type != "lwt_offline":
        cuerpo.update(
            time_quality="sntp_synced", uptime_s=5, occurred_at=datetime.now(tz=timezone.utc).isoformat()
        )
    return _Mensaje(f"farmacias/{DEV}/eventos", json.dumps(cuerpo))


def _lectura(boot_id: int, seq_no: int) -> _Mensaje:
    return _Mensaje(
        f"farmacias/{DEV}/lecturas",
        json.dumps(
            {
                "device_id": DEV,
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "reading_id": f"hu13-{boot_id}-{seq_no}",
                "boot_id": boot_id,
                "seq_no": seq_no,
                "time_quality": "synced",
                "temperatura_interna": 5.0,
                "temperatura_ambiental": 21.0,
                "humedad_ambiental": 50.0,
                "apertura_refrigerador": False,
                "estado_conectividad": "online",
            }
        ),
    )


async def _dispositivo(factory) -> DeviceModel:
    async with factory() as session:
        return await session.get(DeviceModel, DEV)


async def _acciones(factory) -> list[tuple[str, dict]]:
    async with factory() as session:
        filas = (await session.execute(select(AuditLogModel).order_by(AuditLogModel.created_at))).scalars()
        return [(f.accion, f.detalle) for f in filas]


@pytest.mark.asyncio
async def test_online_marca_conectado_con_su_arranque(entorno):
    espia = _Espia(entorno)
    await main._procesar_evento_mqtt(_evento("online", boot_id=5), espia)

    dispositivo = await _dispositivo(entorno)
    assert dispositivo.estado_conectividad == "online"
    assert dispositivo.conectividad_boot_id == 5
    assert dispositivo.conectividad_cambiada_en is not None
    [cambio] = espia.de_tipo("conectividad")
    assert cambio["estado"] == "online"
    assert cambio["origen"] == "online"
    assert cambio["boot_id"] == 5
    assert ("DISPOSITIVO_ONLINE", "online") in [(a, d.get("origen")) for a, d in await _acciones(entorno)]


@pytest.mark.asyncio
async def test_lwt_de_la_sesion_vigente_marca_offline(entorno):
    espia = _Espia(entorno)
    await main._procesar_evento_mqtt(_evento("online", boot_id=5), espia)
    await main._procesar_evento_mqtt(_evento("lwt_offline", boot_id=5), espia)

    dispositivo = await _dispositivo(entorno)
    assert dispositivo.estado_conectividad == "offline"
    assert dispositivo.conectividad_boot_id == 5  # el LWT no cambia el arranque vigente
    assert espia.de_tipo("conectividad")[-1]["estado"] == "offline"
    assert "DISPOSITIVO_OFFLINE" in [a for a, _ in await _acciones(entorno)]


@pytest.mark.asyncio
async def test_lwt_de_un_arranque_anterior_se_ignora(entorno):
    """Criterio 3: el nodo reinició (boot 6) y ya está online; el LWT atrasado
    de la sesión del boot 5 no describe el presente."""
    espia = _Espia(entorno)
    await main._procesar_evento_mqtt(_evento("online", boot_id=6), espia)
    await main._procesar_evento_mqtt(_evento("lwt_offline", boot_id=5), espia)

    assert (await _dispositivo(entorno)).estado_conectividad == "online"
    assert [e["estado"] for e in espia.de_tipo("conectividad")] == ["online"]
    assert espia.de_tipo("lwt_offline") == []
    ignorados = [d for a, d in await _acciones(entorno) if a == "LWT_OBSOLETO_IGNORADO"]
    assert ignorados == [{"boot_id_lwt": 5, "boot_id_vigente": 6}]


@pytest.mark.asyncio
async def test_lwt_sin_boot_id_de_firmware_anterior_se_aplica(entorno):
    """Criterio 7: compatibilidad con un LWT que no trae boot_id."""
    espia = _Espia(entorno)
    await main._procesar_evento_mqtt(_evento("online", boot_id=6), espia)
    await main._procesar_evento_mqtt(_evento("lwt_offline"), espia)
    assert (await _dispositivo(entorno)).estado_conectividad == "offline"


@pytest.mark.asyncio
async def test_la_telemetria_corrige_un_lwt_tardio(entorno):
    """Criterio 4: reconexión rápida con el mismo arranque; el LWT llegó después
    del `online`. La siguiente lectura devuelve el estado a online."""
    espia = _Espia(entorno)
    await main._procesar_evento_mqtt(_evento("online", boot_id=7), espia)
    await main._procesar_evento_mqtt(_evento("lwt_offline", boot_id=7), espia)
    await main._procesar_lectura_mqtt(_lectura(7, 1), espia, None)
    await main._procesar_lectura_mqtt(_lectura(7, 2), espia, None)

    assert (await _dispositivo(entorno)).estado_conectividad == "online"
    cambios = espia.de_tipo("conectividad")
    assert [(c["estado"], c["origen"]) for c in cambios] == [
        ("online", "online"), ("offline", "lwt"), ("online", "telemetria"),
    ]  # la segunda lectura no repite el cambio


@pytest.mark.asyncio
async def test_el_evento_sse_se_publica_despues_del_commit(entorno):
    espia = _Espia(entorno)
    await main._procesar_evento_mqtt(_evento("online", boot_id=8), espia)
    await main._procesar_evento_mqtt(_evento("lwt_offline", boot_id=8), espia)
    assert espia.estado_confirmado_al_publicar == ["online", "offline"]


def test_estado_operativo_expone_desde_cuando(client, token_tecnico, entorno):
    from tests.conftest import auth_header

    respuesta = client.get(
        "/api/dispositivos/estado-operativo", params={"todos": True}, headers=auth_header(token_tecnico)
    )
    assert respuesta.status_code == 200
    assert "conectividad_cambiada_en" in respuesta.json()[0]
