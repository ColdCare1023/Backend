"""Outcomes 2 a 6: evidencia de la excursión, falla de sensor, modelo rechazado,
rechazos auditados y estado del aviso por correo."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.application.use_cases.registrar_lectura_termica import RegistrarLecturaTermicaUseCase
from src.domain.entities.lectura_termica import LecturaTermica
from src.infrastructure.database.models import (
    AuditLogModel,
    IncidenteOperativoModel,
    ThermalAlertModel,
    TraceabilityRecordModel,
)
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.incidente_repository import SQLAlchemyIncidenteRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.interface import main
from src.interface.api.mappers import alerta_to_response
from src.interface.limitador_auditoria import LIMITE_AUDITORIA

DEVICE_ID = "FARM-OUT26-01"
_BASE = datetime.now(tz=timezone.utc).replace(microsecond=0) - timedelta(hours=3)


class _Modelo:
    metadata = {"model_version": "doble"}

    def inferir(self, features):  # pragma: no cover - no se usa con IA desactivada
        raise AssertionError

    inferir_solo_regla = inferir


def _use_case(session) -> RegistrarLecturaTermicaUseCase:
    return RegistrarLecturaTermicaUseCase(
        SQLAlchemyLecturaRepository(session),
        SQLAlchemyAlertaRepository(session),
        SQLAlchemyTrazabilidadRepository(session),
        ClasificarRiesgoTermicoUseCase(_Modelo(), "desactivado"),
        device_repository=SQLAlchemyDeviceRepository(session),
        registro_dispositivos_estricto=False,
        audit_log_repository=SQLAlchemyAuditLogRepository(session),
        ia_legado_modo="desactivado",
        incidente_repository=SQLAlchemyIncidenteRepository(session),
    )


def _lectura(minuto: int, temperatura: float | None) -> LecturaTermica:
    return LecturaTermica(
        device_id=DEVICE_ID,
        timestamp=_BASE + timedelta(minutes=minuto),
        temperatura_ambiental=21.0,
        humedad_ambiental=55.0,
        temperatura_interna=temperatura,
        apertura_refrigerador=False,
        estado_conectividad="online",
    )


async def _correr(db_session_factory, temperaturas: list[float | None]):
    use_case = _use_case(None) if False else None
    async with db_session_factory() as session:
        use_case = _use_case(session)
        for minuto, t in enumerate(temperaturas):
            await use_case.execute(_lectura(minuto, t))
        await session.commit()
    return use_case


async def _todos(db_session_factory, modelo):
    async with db_session_factory() as session:
        return (await session.execute(select(modelo))).scalars().all()


# ── Outcome 2: la excursión queda completa como evidencia ──────────────────────


async def test_la_alerta_guarda_inicio_pico_duracion_e_historica(db_session_factory):
    # 9.0 -> 11.5 -> 10.0 -> normal: pico 11.5; las lecturas son de hace 3 h (diferidas)
    await _correr(db_session_factory, [5.0, 9.0, 11.5, 10.0, 5.0])

    (alerta,) = await _todos(db_session_factory, ThermalAlertModel)
    assert alerta.iniciada_en is not None
    assert alerta.iniciada_en.replace(tzinfo=timezone.utc) == _BASE + timedelta(minutes=1)  # hora de captura
    assert alerta.temperatura_pico == 11.5
    assert alerta.historica is True
    assert alerta.cerrada_en is not None
    respuesta = alerta_to_response(
        await _entidad(db_session_factory)
    )
    assert respuesta.duracion_s == pytest.approx(3 * 60, abs=1)  # minuto 1 -> cierre en el minuto 4
    assert respuesta.temperatura_pico == 11.5 and respuesta.historica is True


async def _entidad(db_session_factory):
    async with db_session_factory() as session:
        (alerta,) = await SQLAlchemyAlertaRepository(session).listar()
        return alerta


async def test_el_cierre_deja_alerta_cerrada_en_la_cadena(db_session_factory):
    await _correr(db_session_factory, [5.0, 9.0, 5.0])

    eventos = [r.tipo_evento for r in await _todos(db_session_factory, TraceabilityRecordModel)]
    assert "ALERTA_TERMICA" in eventos
    assert eventos.count("ALERTA_CERRADA") == 1


# ── Outcome 3: la falla de sensor genera alerta ────────────────────────────────


async def test_la_segunda_lectura_con_sensor_en_falla_abre_el_incidente(db_session_factory):
    use_case = await _correr(db_session_factory, [5.0, None])
    assert await _todos(db_session_factory, IncidenteOperativoModel) == []  # una falla aislada no alerta
    assert use_case.avisos_incidente == []

    use_case = await _correr_continuando(db_session_factory, 2, [None])
    (incidente,) = await _todos(db_session_factory, IncidenteOperativoModel)
    assert incidente.tipo == "sensor_fault" and incidente.resuelto_en is None
    assert [a.tipo for a in use_case.avisos_incidente] == ["sensor_fault"]


async def _correr_continuando(db_session_factory, desde: int, temperaturas):
    async with db_session_factory() as session:
        use_case = _use_case(session)
        for i, t in enumerate(temperaturas):
            await use_case.execute(_lectura(desde + i, t))
        await session.commit()
    return use_case


async def test_la_siguiente_lectura_valida_cierra_el_incidente_de_sensor(db_session_factory):
    await _correr(db_session_factory, [5.0, None, None])
    use_case = await _correr_continuando(db_session_factory, 3, [5.0])

    (incidente,) = await _todos(db_session_factory, IncidenteOperativoModel)
    assert incidente.resuelto_en is not None
    assert [c.tipo for c in use_case.cambios_incidente] == ["resuelto"]


async def test_una_falla_sostenida_no_abre_otro_incidente(db_session_factory):
    await _correr(db_session_factory, [5.0, None, None, None, None])
    assert len(await _todos(db_session_factory, IncidenteOperativoModel)) == 1


# ── Outcome 4: el modelo alterado deja rastro ──────────────────────────────────


async def test_un_modelo_rechazado_se_audita_al_arrancar(db_session_factory, monkeypatch):
    class _Ajustes:
        ia_legado_modo = "activo"
        ia_preventivo_modo = "desactivado"
        ia_preventivo_version = ""
        ia_preventivo_sha256 = ""

    class _Servicio:
        def motivo_rechazo(self):
            return "Checksum SHA-256 inválido para el artefacto del modelo"

    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    monkeypatch.setattr(main, "get_random_forest_service", lambda: _Servicio())

    await main._auditar_modelos_rechazados(_Ajustes())

    filas = [f for f in await _todos(db_session_factory, AuditLogModel) if f.accion == "MODELO_RECHAZADO"]
    assert len(filas) == 1
    assert "Checksum" in json.dumps(filas[0].detalle)


async def test_un_modelo_sano_no_deja_fila(db_session_factory, monkeypatch):
    class _Ajustes:
        ia_legado_modo = "activo"
        ia_preventivo_modo = "desactivado"
        ia_preventivo_version = ""
        ia_preventivo_sha256 = ""

    class _Servicio:
        def motivo_rechazo(self):
            return None

    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    monkeypatch.setattr(main, "get_random_forest_service", lambda: _Servicio())

    await main._auditar_modelos_rechazados(_Ajustes())

    assert [f for f in await _todos(db_session_factory, AuditLogModel) if f.accion == "MODELO_RECHAZADO"] == []


# ── Outcome 5: todo rechazo queda auditado ─────────────────────────────────────


class _Mensaje:
    def __init__(self, topico: str, cuerpo: bytes) -> None:
        self.topic = topico
        self.payload = cuerpo


class _Cliente:
    def __init__(self) -> None:
        self.publicados: list = []

    async def publish(self, topic, payload=None, qos=0, **_kw):
        self.publicados.append((topic, payload))


class _Broadcaster:
    async def publicar(self, *_a, **_k):
        return None


@pytest.fixture
def ingesta(monkeypatch, db_session_factory):
    LIMITE_AUDITORIA.reiniciar()
    monkeypatch.setattr(main, "_session_factory", db_session_factory)


def _telemetria(device_id: str, reading_id: str, temperatura: float = 4.0, timestamp: str | None = None) -> dict:
    return {
        "device_id": device_id, "timestamp": timestamp or datetime.now(tz=timezone.utc).isoformat(),
        "temperatura_interna": temperatura, "temperatura_ambiental": 20.0, "humedad_ambiental": 50.0,
        "apertura_refrigerador": False, "estado_conectividad": "online", "reading_id": reading_id,
    }


async def _acciones(db_session_factory) -> list[str]:
    return [f.accion for f in await _todos(db_session_factory, AuditLogModel)]


async def test_device_id_distinto_del_topico_se_audita_y_no_se_acusa(ingesta, db_session_factory):
    cliente = _Cliente()
    cuerpo = json.dumps(_telemetria("OTRO-NODO", "r1")).encode()

    for _ in range(3):
        await main._procesar_lectura_mqtt(
            _Mensaje(f"farmacias/{DEVICE_ID}/lecturas", cuerpo), _Broadcaster(), cliente
        )

    assert cliente.publicados == []  # esa identidad no es confiable: sin acuse
    acciones = await _acciones(db_session_factory)
    assert acciones.count("LECTURA_RECHAZADA_SUPLANTACION") == 1  # tope por ventana
    fila = next(f for f in await _todos(db_session_factory, AuditLogModel) if f.accion == "LECTURA_RECHAZADA_SUPLANTACION")
    assert fila.detalle["device_id_payload"] == "OTRO-NODO"
    assert fila.detalle["device_id_topico"] == DEVICE_ID


async def test_payload_de_mas_de_5kb_se_audita_con_tope(ingesta, db_session_factory):
    cuerpo = b"x" * (main.MAX_BYTES_PAYLOAD_MQTT + 1)

    for _ in range(5):
        await main._procesar_lectura_mqtt(
            _Mensaje(f"farmacias/{DEVICE_ID}/lecturas", cuerpo), _Broadcaster(), _Cliente()
        )

    assert (await _acciones(db_session_factory)).count("LECTURA_RECHAZADA_TAMANO") == 1


async def test_colision_de_clave_abre_incidente_y_avisa(ingesta, db_session_factory):
    async with db_session_factory() as session:
        await SQLAlchemyDeviceRepository(session).obtener_o_crear(DEVICE_ID)
        await session.commit()
    ts = datetime.now(tz=timezone.utc).isoformat()
    primero = json.dumps(_telemetria(DEVICE_ID, "dup-1", 4.0, ts)).encode()
    distinto = json.dumps(_telemetria(DEVICE_ID, "dup-1", 6.5, ts)).encode()
    topico = f"farmacias/{DEVICE_ID}/lecturas"
    cliente = _Cliente()

    class _Despachador:
        def __init__(self) -> None:
            self.avisos = []

        def encolar(self, aviso):
            self.avisos.append(aviso)

        def encolar_todos(self, avisos):
            self.avisos.extend(avisos)

    despachador = _Despachador()
    await main._procesar_lectura_mqtt(_Mensaje(topico, primero), _Broadcaster(), cliente, despachador)
    await main._procesar_lectura_mqtt(_Mensaje(topico, distinto), _Broadcaster(), cliente, despachador)
    await main._procesar_lectura_mqtt(_Mensaje(topico, distinto), _Broadcaster(), cliente, despachador)

    (incidente,) = await _todos(db_session_factory, IncidenteOperativoModel)
    assert incidente.tipo == "colision_clave"
    assert [a.tipo for a in despachador.avisos] == ["colision_clave"]  # un aviso por incidente
    assert "LECTURA_CONFLICTO" in await _acciones(db_session_factory)


# ── Outcome 6: un correo fallido se ve ─────────────────────────────────────────


async def test_la_respuesta_de_la_alerta_expone_el_estado_del_correo(db_session_factory):
    await _correr(db_session_factory, [5.0, 9.0])
    entidad = await _entidad(db_session_factory)
    assert alerta_to_response(entidad).notificada_en is None
    assert alerta_to_response(entidad).notificacion_intentos == 0

    entidad.notificacion_intentos = 2
    entidad.notificada_en = datetime.now(tz=timezone.utc)
    respuesta = alerta_to_response(entidad)
    assert respuesta.notificacion_intentos == 2 and respuesta.notificada_en is not None
