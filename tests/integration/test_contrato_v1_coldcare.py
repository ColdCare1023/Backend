"""Ingesta del contrato v1.0 del firmware ColdCare1023/IoT.

Las entradas son los mensajes que genera el firmware real
(`tests/fixtures/contrato_v1/`, copiados de ColdCare1023/Backend
`contrato/ejemplos/`). Solo se ajusta `captured_at` a "ahora" para que no caiga
fuera de la ventana de antigüedad máxima (48 h) y, donde hace falta, `seq_no`
para no chocar entre casos.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.infrastructure.database.models import AuditLogModel
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.interface import main

EJEMPLOS = Path(__file__).resolve().parent.parent / "fixtures" / "contrato_v1"
DEV = "ESP32_FARMACIA_01"
TOPIC = f"farmacias/{DEV}/lecturas"


class _Mensaje:
    def __init__(self, topic: str, payload: str) -> None:
        self.topic = topic
        self.payload = payload.encode("utf-8")


class _Espia:
    def __init__(self) -> None:
        self.acks: list[dict] = []

    async def publish(self, topic, payload=None, qos=0, **_kw):
        self.acks.append(json.loads(payload))


class _Mudo:
    def __init__(self) -> None:
        self.eventos: list[tuple[dict, str]] = []

    async def publicar(self, evento, tipo="lectura"):
        self.eventos.append((evento, tipo))


def _ejemplo(nombre: str, **cambios) -> dict:
    datos = json.loads((EJEMPLOS / f"{nombre}.json").read_text(encoding="utf-8"))
    if datos.get("captured_at"):
        datos["captured_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    datos.update(cambios)
    return datos


@pytest_asyncio.fixture
async def entorno(db_session_factory, monkeypatch):
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    async with db_session_factory() as session:
        repo = SQLAlchemyDeviceRepository(session)
        await repo.obtener_o_crear(DEV)
        await repo.obtener_o_crear("ESP32_DEMO_01")
        await session.commit()
    return db_session_factory


async def _enviar(datos: dict, espia: _Espia, topic: str = TOPIC) -> dict:
    await main._procesar_lectura_mqtt(_Mensaje(topic, json.dumps(datos)), _Mudo(), espia)
    return espia.acks[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "nombre",
    [
        "telemetria_normal",
        "telemetria_con_respaldo",
        "telemetria_ds18b20_crc_error",
        "telemetria_ds18b20_desconectado",
        "telemetria_sht31_desconectado",
        "telemetria_puerta_prolongada",
        "telemetria_retrofechada",
        "telemetria_sin_hora",
        "telemetria_sin_mc38",
    ],
)
async def test_mensajes_reales_del_firmware_se_persisten_y_acusan(entorno, nombre):
    espia = _Espia()
    datos = _ejemplo(nombre)

    ack = await _enviar(datos, espia)

    # Acuse en el formato que parsea el firmware (core/Ack.cpp).
    assert ack["status"] == "committed"
    assert (ack["device_id"], ack["boot_id"], ack["seq_no"]) == (DEV, datos["boot_id"], datos["seq_no"])
    async with entorno() as session:
        lectura = await SQLAlchemyLecturaRepository(session).obtener_por_device_boot_seq(
            DEV, datos["boot_id"], datos["seq_no"]
        )
    assert lectura is not None
    assert lectura.temperatura_interna == datos["temp_ds18b20"]
    assert lectura.humedad_ambiental == datos["humedad_sht31"]
    assert lectura.apertura_refrigerador == datos["door_open"]
    assert lectura.uptime_ms == datos["uptime_s"] * 1000
    assert lectura.payload["contrato"] == "1.0"


@pytest.mark.asyncio
async def test_falla_ds18b20_no_alimenta_la_ia(entorno):
    """Regla 1 del contrato: con SENSOR_FAULT no se calcula excursión ni inferencia."""
    espia = _Espia()
    datos = _ejemplo("telemetria_ds18b20_desconectado")
    await _enviar(datos, espia)

    async with entorno() as session:
        lectura = await SQLAlchemyLecturaRepository(session).obtener_por_device_boot_seq(
            DEV, datos["boot_id"], datos["seq_no"]
        )
    assert lectura.temperatura_interna is None
    assert lectura.origen_clasificacion == "fallo_sensor"
    assert lectura.payload["ds18b20_fault"] == datos["ds18b20_fault"]


@pytest.mark.asyncio
async def test_sin_mc38_la_puerta_queda_null(entorno):
    espia = _Espia()
    datos = _ejemplo("telemetria_sin_mc38")
    await _enviar(datos, espia)
    async with entorno() as session:
        lectura = await SQLAlchemyLecturaRepository(session).obtener_por_device_boot_seq(
            DEV, datos["boot_id"], datos["seq_no"]
        )
    assert lectura.apertura_refrigerador is None


@pytest.mark.asyncio
async def test_reenvio_es_duplicate_y_contenido_distinto_va_a_cuarentena(entorno):
    espia = _Espia()
    datos = _ejemplo("telemetria_normal")
    assert (await _enviar(datos, espia))["status"] == "committed"
    assert (await _enviar(datos, espia))["status"] == "duplicate"

    # BE-001: un conflicto no autoriza a borrar la copia del nodo: cuarentena.
    alterada = dict(datos, temp_ds18b20=datos["temp_ds18b20"] + 3)
    assert (await _enviar(alterada, espia))["status"] == "rejected"
    async with entorno() as session:
        conflictos = (
            await session.execute(select(AuditLogModel).where(AuditLogModel.accion == "LECTURA_CONFLICTO"))
        ).scalars().all()
    assert len(conflictos) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cambios",
    [
        {"temp_ds18b20": None},                      # null con status OK
        {"temp_ds18b20": -127.0, "ds18b20_status": "OK"},  # código de error como número
        {"sensor_fault": True},                      # no refleja los sensores
        {"captured_at": None},                       # null sin unsynced
        {"campo_extra": 1},                          # extra="forbid"
        {"firmware_version": "1.0.0"},               # sin la v
    ],
)
async def test_mutaciones_invalidas_se_rechazan_con_identidad(entorno, cambios):
    """HU-15: rechazo con acuse `rejected` → el nodo lo pasa a cuarentena y sigue."""
    espia = _Espia()
    datos = _ejemplo("telemetria_normal", seq_no=7, **cambios)
    ack = await _enviar(datos, espia)
    assert ack["status"] == "rejected"
    assert (ack["boot_id"], ack["seq_no"]) == (42, 7)
    async with entorno() as session:
        assert await SQLAlchemyLecturaRepository(session).obtener_por_device_boot_seq(DEV, 42, 7) is None


@pytest.mark.asyncio
async def test_payload_v1_mayor_a_768_bytes_se_rechaza(entorno):
    espia = _Espia()
    datos = _ejemplo("telemetria_normal", seq_no=8)
    cuerpo = json.dumps(datos) + " " * 800  # JSON válido, tamaño excedido
    await main._procesar_lectura_mqtt(_Mensaje(TOPIC, cuerpo), _Mudo(), espia)
    assert espia.acks[-1]["status"] == "rejected"


@pytest.mark.asyncio
async def test_lectura_unsynced_se_reancla_con_una_sincronizada_del_mismo_arranque(entorno):
    espia = _Espia()
    sin_hora = _ejemplo("telemetria_sin_hora", boot_id=50, seq_no=1, uptime_s=100)
    await _enviar(sin_hora, espia)
    real = datetime.now(timezone.utc).replace(microsecond=0)
    con_hora = _ejemplo("telemetria_normal", boot_id=50, seq_no=2, uptime_s=160,
                        captured_at=real.strftime("%Y-%m-%dT%H:%M:%SZ"))
    await _enviar(con_hora, espia)

    async with entorno() as session:
        lectura = await SQLAlchemyLecturaRepository(session).obtener_por_device_boot_seq(DEV, 50, 1)
    assert lectura.time_quality == "unsynced"
    assert lectura.timestamp_estimado.replace(tzinfo=timezone.utc) == real - timedelta(seconds=60)


@pytest.mark.asyncio
async def test_eventos_v1_se_auditan(entorno):
    mudo = _Mudo()
    for nombre in ("evento_buffer_saturado", "evento_lwt_offline"):
        datos = json.loads((EJEMPLOS / f"{nombre}.json").read_text(encoding="utf-8"))
        await main._procesar_evento_mqtt(_Mensaje(f"farmacias/{DEV}/eventos", json.dumps(datos)), mudo)

    async with entorno() as session:
        acciones = {
            a.accion for a in (await session.execute(select(AuditLogModel))).scalars().all()
        }
    assert {"BUFFER_SATURADO", "DISPOSITIVO_OFFLINE"} <= acciones
    assert [t for _, t in mudo.eventos] == ["buffer_saturado", "lwt_offline"]


@pytest.mark.asyncio
async def test_evento_estado_nodo_va_por_sse_sin_auditar(entorno):
    mudo = _Mudo()
    datos = {"schema_version": "1.0", "device_id": DEV, "event_type": "estado_nodo", "boot_id": 42,
             "time_quality": "sntp_synced", "uptime_s": 300, "detail": {"pendientes": 0}}
    await main._procesar_evento_mqtt(_Mensaje(f"farmacias/{DEV}/eventos", json.dumps(datos)), mudo)
    async with entorno() as session:
        assert (await session.execute(select(AuditLogModel))).scalars().all() == []
    assert mudo.eventos[0][1] == "estado_nodo"
