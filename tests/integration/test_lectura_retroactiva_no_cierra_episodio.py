"""Una lectura en rango capturada antes que la última del episodio crítico
abierto no lo cierra como «recuperación» (BE-001 / HU-21 / HU-53).

Antes, un técnico que cargaba con `POST /api/lecturas` una lectura de 5 °C
con hora de hace 3 h (se admite hasta 24 h atrás) cerraba la alerta de un
equipo que seguía a 9 °C: la cadena guardaba `ALERTA_CERRADA` con
`motivo_cierre=recuperacion` y `cerrada_en` anterior a `iniciada_en`, el SSE
avisaba «recuperacion» y la siguiente lectura en vivo, todavía fuera de rango,
abría otra alerta PENDIENTE con otro correo para la misma excursión. Lo mismo
con una lectura del buffer del nodo que llega después de una en vivo."""

import json
from datetime import datetime, timedelta, timezone
from itertools import count

import pytest
from sqlalchemy import select

from src.infrastructure.config import get_settings
from src.infrastructure.database.models import TraceabilityRecordModel
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.interface import main
from tests.conftest import auth_header

DEV = "FARM-RETRO-01"
_SEQ = count(1)


class _Mensaje:
    def __init__(self, payload: str) -> None:
        self.topic = f"farmacias/{DEV}/lecturas"
        self.payload = payload.encode("utf-8")


class _Broadcaster:
    def __init__(self) -> None:
        self.tipos: list[str] = []

    async def publicar(self, evento, tipo="lectura"):
        self.tipos.append(tipo)


class _Notificaciones:
    def __init__(self) -> None:
        self.avisos: list = []

    def encolar(self, aviso):
        self.avisos.append(aviso)
        return True

    def encolar_todos(self, avisos):
        for aviso in avisos:
            self.encolar(aviso)


@pytest.fixture
async def entorno(db_session_factory, monkeypatch):
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    monkeypatch.setenv("IA_LEGADO_MODO", "experimental")
    monkeypatch.setenv("IA_PREVENTIVO_MODO", "desactivado")
    get_settings.cache_clear()
    async with db_session_factory() as session:
        await SQLAlchemyDeviceRepository(session).obtener_o_crear(DEV)
        await session.commit()
    yield _Notificaciones(), _Broadcaster()
    get_settings.cache_clear()


async def _mqtt(notificaciones, broadcaster, instante: datetime, temperatura: float) -> None:
    datos = {
        "device_id": DEV, "timestamp": instante.isoformat(), "schema_version": 1,
        "reading_id": f"retro-{next(_SEQ)}", "temperatura_interna": temperatura,
        "temperatura_ambiental": 22.0, "humedad_ambiental": 55.0,
        "apertura_refrigerador": False, "estado_conectividad": "online", "time_quality": "synced",
    }
    await main._procesar_lectura_mqtt(_Mensaje(json.dumps(datos)), broadcaster, None, notificaciones)


def _alertas(client, token, **filtros) -> list[dict]:
    respuesta = client.get("/api/alertas", params={"device_id": DEV, **filtros}, headers=auth_header(token))
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


async def _eventos(db_session_factory, tipo: str) -> list[dict]:
    async with db_session_factory() as session:
        filas = (await session.execute(
            select(TraceabilityRecordModel).order_by(TraceabilityRecordModel.chain_seq)
        )).scalars()
        return [f.payload for f in filas if f.tipo_evento == tipo and f.payload.get("device_id") == DEV]


async def test_manual_retroactiva_en_rango_no_cierra_la_alerta_ni_duplica_el_aviso(
    client, token_farmaceutico, token_tecnico, entorno, db_session_factory
):
    notificaciones, broadcaster = entorno
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    await _mqtt(notificaciones, broadcaster, ahora - timedelta(seconds=60), 9.0)
    [alerta] = _alertas(client, token_farmaceutico, estado="pendiente")
    avisos = len(notificaciones.avisos)
    assert avisos == 1

    respuesta = client.post(
        "/api/lecturas",
        json={"device_id": DEV, "timestamp": (ahora - timedelta(hours=3)).isoformat(), "temperatura_interna": 5.0},
        headers=auth_header(token_tecnico),
    )

    assert respuesta.status_code == 201, respuesta.text
    [misma] = _alertas(client, token_farmaceutico)
    assert misma["id"] == alerta["id"]
    assert misma["episodio_abierto"] is True and misma["estado"] == "pendiente"
    assert await _eventos(db_session_factory, "ALERTA_CERRADA") == []

    # El equipo sigue a 9 °C: la misma alerta, sin otra PENDIENTE ni otro correo.
    await _mqtt(notificaciones, broadcaster, ahora - timedelta(seconds=30), 9.0)
    [sigue] = _alertas(client, token_farmaceutico)
    assert sigue["id"] == alerta["id"] and sigue["episodio_abierto"] is True
    assert len(notificaciones.avisos) == avisos


async def test_lectura_del_buffer_en_rango_tras_una_en_vivo_no_avisa_recuperacion(
    client, token_farmaceutico, entorno, db_session_factory
):
    notificaciones, broadcaster = entorno
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    await _mqtt(notificaciones, broadcaster, ahora - timedelta(seconds=60), 9.0)
    broadcaster.tipos.clear()

    await _mqtt(notificaciones, broadcaster, ahora - timedelta(minutes=40), 5.0)

    assert "recuperacion" not in broadcaster.tipos
    [alerta] = _alertas(client, token_farmaceutico)
    assert alerta["episodio_abierto"] is True
    assert await _eventos(db_session_factory, "ALERTA_CERRADA") == []

    # La vuelta real a rango (posterior a la última lectura) sí la cierra.
    broadcaster.tipos.clear()
    await _mqtt(notificaciones, broadcaster, ahora - timedelta(seconds=10), 5.0)
    assert "recuperacion" in broadcaster.tipos
    [cierre] = await _eventos(db_session_factory, "ALERTA_CERRADA")
    assert cierre["motivo_cierre"] == "recuperacion"
    assert cierre["cerrada_en"] >= cierre["iniciada_en"]
