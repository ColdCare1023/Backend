"""HU-53: notificación por correo de excursión crítica.

Se ejercita el camino real —consumidor MQTT → caso de uso → COMMIT → cola →
despachador→ transporte— con un transporte espía en lugar del proveedor."""

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.config import Settings
from src.infrastructure.database.models import AuditLogModel, ThermalAlertModel
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.notifications.despachador import DespachadorNotificaciones
from src.infrastructure.notifications.transporte_email import ErrorEnvioCorreo
from src.interface import main

DEVICE_ID = "FARM-HU53-01"
TOPIC = f"farmacias/{DEVICE_ID}/lecturas"


class _Mensaje:
    def __init__(self, payload: str) -> None:
        self.topic = TOPIC
        self.payload = payload.encode("utf-8")


class _BroadcasterMudo:
    async def publicar(self, evento, tipo="lectura"):
        return None


class _TransporteEspia:
    nombre = "espia"

    def __init__(self, fallos: int = 0, demora: float = 0.0) -> None:
        self.enviados = []
        self._fallos = fallos
        self._demora = demora

    async def enviar(self, mensaje) -> None:
        if self._demora:
            await asyncio.sleep(self._demora)
        if self._fallos > 0:
            self._fallos -= 1
            raise ErrorEnvioCorreo("SMTPServerDisconnected: conexión cerrada")
        self.enviados.append(mensaje)


def _settings(**extra) -> Settings:
    base = {
        "environment": "test",
        "email_enabled": True,
        "email_from": "avisos@coldcare.pe",
        "email_destino_respaldo": "qf-respaldo@coldcare.pe",
        "frontend_url": "https://coldcare.vercel.app",
    }
    base.update(extra)
    return Settings(**base)


_SEQ = iter(range(1, 10_000))


def _lectura(temperatura: float, minutos_atras: float = 0.0) -> _Mensaje:
    ahora = datetime.now(tz=timezone.utc)
    return _Mensaje(
        json.dumps(
            {
                "device_id": DEVICE_ID,
                "timestamp": (ahora - timedelta(minutes=minutos_atras)).isoformat(),
                "schema_version": 1,
                "reading_id": f"hu53-{next(_SEQ)}",
                "temperatura_interna": temperatura,
                "temperatura_ambiental": 22.0,
                "humedad_ambiental": 55.0,
                "apertura_refrigerador": False,
                "estado_conectividad": "online",
            }
        )
    )


@pytest_asyncio.fixture
async def entorno(db_session_factory, monkeypatch):
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    async with db_session_factory() as session:
        await SQLAlchemyDeviceRepository(session).obtener_o_crear(DEVICE_ID)
        await session.commit()
    return db_session_factory


def _despachador(factory, transporte, **settings) -> DespachadorNotificaciones:
    return DespachadorNotificaciones(_settings(**settings), factory, transporte, esperas_reintento=(3600,))


async def _procesar_cola(despachador: DespachadorNotificaciones) -> list[str]:
    estados = []
    while despachador.pendientes:
        aviso = despachador._cola.get_nowait()
        estados.append((await despachador.procesar(aviso)).estado)
        despachador._cola.task_done()
    return estados


async def _acciones(factory, accion: str) -> list[dict]:
    async with factory() as session:
        filas = (await session.execute(select(AuditLogModel).where(AuditLogModel.accion == accion))).scalars()
        return [f.detalle for f in filas]


async def _alerta(factory) -> ThermalAlertModel:
    async with factory() as session:
        return (await session.execute(select(ThermalAlertModel))).scalars().one()


@pytest.mark.asyncio
async def test_apertura_critica_envia_un_correo_despues_del_commit(entorno):
    transporte = _TransporteEspia()
    despachador = _despachador(entorno, transporte)

    await main._procesar_lectura_mqtt(_lectura(12.0), _BroadcasterMudo(), None, despachador)

    assert despachador.pendientes == 1  # encolado, todavía no enviado
    assert await _procesar_cola(despachador) == ["enviado"]
    assert len(transporte.enviados) == 1
    correo = transporte.enviados[0]
    assert correo.destinatario == "qf-respaldo@coldcare.pe"
    assert "12.0 °C" in correo.cuerpo
    alerta = await _alerta(entorno)
    assert alerta.notificada_en is not None
    enviados = await _acciones(entorno, "NOTIFICACION_ENVIADA")
    assert len(enviados) == 1
    assert enviados[0]["destinatario"] == "q***@coldcare.pe"
    assert enviados[0]["latencia_s"] is not None


@pytest.mark.asyncio
async def test_sin_commit_no_se_encola_ningun_aviso(entorno, monkeypatch):
    despachador = _despachador(entorno, _TransporteEspia())

    async def commit_que_falla(self):
        raise RuntimeError("la base de datos rechazó el COMMIT")

    monkeypatch.setattr(AsyncSession, "commit", commit_que_falla)
    with pytest.raises(RuntimeError):
        await main._procesar_lectura_mqtt(_lectura(12.0), _BroadcasterMudo(), None, despachador)
    assert despachador.pendientes == 0


@pytest.mark.asyncio
async def test_excursion_sostenida_produce_un_solo_correo(entorno):
    transporte = _TransporteEspia()
    despachador = _despachador(entorno, transporte)
    for minuto in range(10, 0, -1):
        await main._procesar_lectura_mqtt(_lectura(12.0, minuto), _BroadcasterMudo(), None, despachador)
    await _procesar_cola(despachador)
    assert len(transporte.enviados) == 1


@pytest.mark.asyncio
async def test_recaida_dentro_de_la_ventana_no_reenvia(entorno):
    """P3: la reapertura dentro de ALERTA_VENTANA_NORMALIZACION_MINUTOS es el
    mismo episodio, ya avisado."""
    transporte = _TransporteEspia()
    despachador = _despachador(entorno, transporte)
    await main._procesar_lectura_mqtt(_lectura(12.0), _BroadcasterMudo(), None, despachador)
    aviso = despachador._cola.get_nowait()
    despachador._cola.task_done()
    assert (await despachador.procesar(aviso)).estado == "enviado"

    # GenerarAlertaUseCase emite un aviso con el MISMO alerta_id al reabrir
    # (test_notificacion_episodio::test_reapertura_dentro_de_la_ventana_...).
    despachador.encolar(aviso)
    assert await _procesar_cola(despachador) == ["ya_notificado"]
    assert len(transporte.enviados) == 1


@pytest.mark.asyncio
async def test_reinicio_del_backend_no_duplica_el_correo(entorno):
    transporte = _TransporteEspia()
    primero = _despachador(entorno, transporte)
    await main._procesar_lectura_mqtt(_lectura(12.0), _BroadcasterMudo(), None, primero)
    await _procesar_cola(primero)

    # Un despachador nuevo (proceso reiniciado) recupera solo lo no notificado.
    reiniciado = _despachador(entorno, transporte)
    assert await reiniciado.recuperar_pendientes() == 0
    assert len(transporte.enviados) == 1


@pytest.mark.asyncio
async def test_aviso_perdido_en_un_reinicio_se_recupera_al_arrancar(entorno):
    transporte = _TransporteEspia()
    perdido = _despachador(entorno, transporte)
    await main._procesar_lectura_mqtt(_lectura(12.0), _BroadcasterMudo(), None, perdido)
    # El proceso muere con el aviso en la cola en memoria.

    reiniciado = _despachador(entorno, transporte)
    assert await reiniciado.recuperar_pendientes() == 1
    assert await _procesar_cola(reiniciado) == ["enviado"]


@pytest.mark.asyncio
async def test_fallos_del_proveedor_se_reintentan_y_auditan(entorno):
    transporte = _TransporteEspia(fallos=2)
    despachador = _despachador(entorno, transporte)
    await main._procesar_lectura_mqtt(_lectura(12.0), _BroadcasterMudo(), None, despachador)
    aviso = despachador._cola.get_nowait()

    resultados = [await despachador.procesar(aviso) for _ in range(3)]
    await despachador.detener(timeout=0.1)

    assert [r.estado for r in resultados] == ["fallido", "fallido", "enviado"]
    fallidos = await _acciones(entorno, "NOTIFICACION_FALLIDA")
    assert [f["intento"] for f in fallidos] == [1, 2]
    assert "SMTPServerDisconnected" in fallidos[0]["error"]
    assert len(await _acciones(entorno, "NOTIFICACION_ENVIADA")) == 1
    assert (await _alerta(entorno)).notificacion_intentos == 2


@pytest.mark.asyncio
async def test_agotados_los_intentos_no_se_insiste(entorno):
    despachador = _despachador(entorno, _TransporteEspia(fallos=99), notificacion_max_intentos=2)
    await main._procesar_lectura_mqtt(_lectura(12.0), _BroadcasterMudo(), None, despachador)
    aviso = despachador._cola.get_nowait()
    resultados = [await despachador.procesar(aviso) for _ in range(3)]
    await despachador.detener(timeout=0.1)

    assert [r.intentos for r in resultados] == [1, 2, 2]
    assert len(await _acciones(entorno, "NOTIFICACION_FALLIDA")) == 2


@pytest.mark.asyncio
async def test_proveedor_lento_no_frena_la_ingesta(entorno):
    despachador = _despachador(entorno, _TransporteEspia(demora=10))
    await despachador.iniciar()
    try:
        await main._procesar_lectura_mqtt(_lectura(12.0, 2), _BroadcasterMudo(), None, despachador)
        await asyncio.sleep(0.05)  # el worker ya está bloqueado en el envío lento
        inicio = time.perf_counter()
        await main._procesar_lectura_mqtt(_lectura(12.5, 1), _BroadcasterMudo(), None, despachador)
        assert time.perf_counter() - inicio < 1.0
    finally:
        await despachador.detener(timeout=0.1)


@pytest.mark.asyncio
async def test_lectura_diferida_se_marca_en_el_asunto(entorno):
    transporte = _TransporteEspia()
    despachador = _despachador(entorno, transporte)
    await main._procesar_lectura_mqtt(_lectura(12.0, minutos_atras=20), _BroadcasterMudo(), None, despachador)
    await _procesar_cola(despachador)
    assert "[DIFERIDA]" in transporte.enviados[0].asunto


@pytest.mark.asyncio
async def test_el_responsable_del_dispositivo_tiene_prioridad(entorno):
    async with entorno() as session:
        await SQLAlchemyDeviceRepository(session).actualizar_responsable(
            DEVICE_ID, nombre="Q.F. Ana", email="ana@farmacia.pe", telefono=None
        )
        await session.commit()
    transporte = _TransporteEspia()
    despachador = _despachador(entorno, transporte)
    await main._procesar_lectura_mqtt(_lectura(12.0), _BroadcasterMudo(), None, despachador)
    await _procesar_cola(despachador)
    assert transporte.enviados[0].destinatario == "ana@farmacia.pe"


@pytest.mark.asyncio
async def test_sin_destinatario_queda_auditado(entorno):
    despachador = _despachador(entorno, _TransporteEspia(), email_destino_respaldo="")
    await main._procesar_lectura_mqtt(_lectura(12.0), _BroadcasterMudo(), None, despachador)
    assert await _procesar_cola(despachador) == ["sin_destinatario"]
    assert len(await _acciones(entorno, "NOTIFICACION_SIN_DESTINATARIO")) == 1
