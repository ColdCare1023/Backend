"""HU-54: incidente SIN_DATOS persistente, umbral N x intervalo del nodo,
aviso después del COMMIT y cierre al volver las lecturas."""

import json
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import select

from src.infrastructure.config import Settings, get_settings
from src.infrastructure.database.models import (
    AuditLogModel,
    DeviceModel,
    IncidenteOperativoModel,
    ThermalReadingModel,
)
from src.infrastructure.notifications.despachador import DespachadorNotificaciones
from src.interface import main
from src.interface.tareas import VigilanteSinDatos
from tests.conftest import auth_header

DEVICE_ID = "FARM-HU54-01"


class _Broadcaster:
    def __init__(self) -> None:
        self.eventos: list[tuple[str, dict]] = []

    async def publicar(self, evento, tipo="lectura"):
        self.eventos.append((tipo, evento))


class _Despachador:
    def __init__(self) -> None:
        self.avisos = []

    def encolar_todos(self, avisos) -> None:
        self.avisos.extend(avisos)


class _TransporteEspia:
    nombre = "espia"

    def __init__(self) -> None:
        self.enviados = []

    async def enviar(self, mensaje) -> None:
        self.enviados.append(mensaje)


@pytest_asyncio.fixture
async def nodo(db_session_factory):
    """Nodo activo con intervalo de 10 s (banco) y una lectura de hace 40 s."""
    ahora = datetime.now(tz=timezone.utc)
    async with db_session_factory() as session:
        session.add(DeviceModel(id=DEVICE_ID, activo=True, intervalo_reporte_segundos=10))
        await session.flush()
        session.add(ThermalReadingModel(
            device_id=DEVICE_ID, timestamp=ahora - timedelta(seconds=40), received_at=ahora - timedelta(seconds=40),
            temperatura_interna=5.0, temperatura_ambiental=21.0, humedad_ambiental=50.0, origen="dispositivo",
        ))
        await session.commit()
    return ahora


def _vigilante(db_session_factory, broadcaster, despachador) -> VigilanteSinDatos:
    return VigilanteSinDatos(db_session_factory, broadcaster, despachador, periodo_segundos=30, n_intervalos=3)


async def _incidentes(db_session_factory) -> list[IncidenteOperativoModel]:
    async with db_session_factory() as session:
        return list((await session.execute(select(IncidenteOperativoModel))).scalars().all())


async def test_el_umbral_es_n_veces_el_intervalo_del_nodo(db_session_factory, nodo):
    broadcaster, despachador = _Broadcaster(), _Despachador()
    # 40 s de silencio ≥ 3 x 10 s: con el intervalo de fábrica (90 s) no habría incidente.
    cambios = await _vigilante(db_session_factory, broadcaster, despachador).ejecutar_una_vez(nodo)

    assert [c.tipo for c in cambios] == ["abierto"]
    incidentes = await _incidentes(db_session_factory)
    assert len(incidentes) == 1 and incidentes[0].umbral_segundos == 30 and incidentes[0].resuelto_en is None
    assert [a.device_id for a in despachador.avisos] == [DEVICE_ID]  # tras el COMMIT
    assert broadcaster.eventos[0][0] == "sin_datos"


async def test_no_duplica_el_incidente_abierto(db_session_factory, nodo):
    vigilante = _vigilante(db_session_factory, _Broadcaster(), _Despachador())
    await vigilante.ejecutar_una_vez(nodo)
    await vigilante.ejecutar_una_vez(nodo + timedelta(seconds=30))

    assert len(await _incidentes(db_session_factory)) == 1


async def test_con_intervalo_de_fabrica_no_hay_incidente_a_los_40_s(db_session_factory, nodo):
    async with db_session_factory() as session:
        (await session.get(DeviceModel, DEVICE_ID)).intervalo_reporte_segundos = 30
        await session.commit()

    cambios = await _vigilante(db_session_factory, _Broadcaster(), _Despachador()).ejecutar_una_vez(nodo)
    assert cambios == []


async def test_la_lectura_siguiente_resuelve_el_incidente(db_session_factory, nodo, monkeypatch):
    await _vigilante(db_session_factory, _Broadcaster(), _Despachador()).ejecutar_una_vez(nodo)
    get_settings.cache_clear()
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    broadcaster = _Broadcaster()

    class _Mensaje:
        topic = f"farmacias/{DEVICE_ID}/lecturas"
        payload = json.dumps({
            "device_id": DEVICE_ID, "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "temperatura_interna": 5.0, "temperatura_ambiental": 21.0, "humedad_ambiental": 50.0,
            "apertura_refrigerador": False, "estado_conectividad": "online", "reading_id": "hu54-r1",
        }).encode()

    await main._procesar_lectura_mqtt(_Mensaje(), broadcaster, None)

    incidentes = await _incidentes(db_session_factory)
    assert incidentes[0].resuelto_en is not None
    assert "sin_datos_resuelto" in [tipo for tipo, _ in broadcaster.eventos]
    async with db_session_factory() as session:
        acciones = (await session.execute(select(AuditLogModel.accion))).scalars().all()
    assert {"SIN_DATOS_DETECTADO", "SIN_DATOS_RESUELTO"} <= set(acciones)
    get_settings.cache_clear()


async def test_el_aviso_sin_datos_se_envia_una_sola_vez(db_session_factory, nodo):
    despachador_falso = _Despachador()
    await _vigilante(db_session_factory, _Broadcaster(), despachador_falso).ejecutar_una_vez(nodo)
    transporte = _TransporteEspia()
    despachador = DespachadorNotificaciones(
        Settings(environment="test", email_enabled=True, email_from="avisos@coldcare.pe",
                 email_destino_respaldo="qf@coldcare.pe"),
        db_session_factory, transporte=transporte,
    )
    aviso = despachador_falso.avisos[0]

    primero = await despachador.procesar(aviso)
    segundo = await despachador.procesar(aviso)

    assert (primero.estado, segundo.estado) == ("enviado", "ya_notificado")
    assert transporte.enviados[0].asunto == f"[ColdCare] SIN DATOS — {DEVICE_ID}"
    assert (await _incidentes(db_session_factory))[0].notificado_en is not None


async def test_estado_nodo_actualiza_latido_e_intervalo(db_session_factory, nodo, monkeypatch):
    monkeypatch.setattr(main, "_session_factory", db_session_factory)

    class _Mensaje:
        topic = f"farmacias/{DEVICE_ID}/eventos"
        payload = json.dumps({
            "schema_version": "1.0", "device_id": DEVICE_ID, "event_type": "estado_nodo", "boot_id": 7,
            "time_quality": "sntp_synced", "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
            "detail": {"intervalo_s": 30, "pendientes": 0},
        }).encode()

    await main._procesar_evento_mqtt(_Mensaje(), _Broadcaster())

    async with db_session_factory() as session:
        dispositivo = await session.get(DeviceModel, DEVICE_ID)
        assert dispositivo.intervalo_reporte_segundos == 30
        assert dispositivo.ultimo_latido_en is not None


async def test_el_estado_operativo_usa_el_intervalo_y_el_incidente_persistente(
    client, token_tecnico, db_session_factory, nodo
):
    await _vigilante(db_session_factory, _Broadcaster(), _Despachador()).ejecutar_una_vez(nodo)

    estados = client.get("/api/dispositivos/estado-operativo", headers=auth_header(token_tecnico)).json()
    estado = next(e for e in estados if e["id"] == DEVICE_ID)
    assert estado["intervalo_reporte_segundos"] == 10
    assert estado["umbral_sin_datos_segundos"] == 30
    assert estado["estado_operativo"] == "SIN_DATOS"
    incidente = estado["incidencias_operativas"][0]
    historial = client.get(f"/api/dispositivos/incidentes?device_id={DEVICE_ID}", headers=auth_header(token_tecnico))
    assert historial.status_code == 200 and historial.json()[0]["id"] == incidente["id"]
