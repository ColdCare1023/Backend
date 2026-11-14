"""HU-48, HU-35 y HU-07 de punta a punta (MQTT → base → API).

- HU-48: el `estado_nodo` y los `buffer_saturado` que publica el firmware
  quedan en la base y `/api/dispositivos/estado-operativo` los expone en
  `buffer_littlefs` (pendientes, rango, pérdidas y brechas con su causa). Antes
  el rango y las brechas solo existían en el modo demo del frontend.
- HU-35: el umbral de puerta abierta configurado sale en el estado operativo.
- HU-07: una lectura de un arranque sin hora se marca `sin_hora` hasta que el
  arranque sincroniza y se reancla."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.infrastructure.config import get_settings
from src.infrastructure.database.models import AuditLogModel, EpisodioBufferSaturadoModel, EstadoNodoModel
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.interface import main
from tests.conftest import auth_header

EJEMPLOS = Path(__file__).resolve().parent.parent / "fixtures" / "contrato_v1"
DEV = "ESP32_FARMACIA_01"
URL = "/api/dispositivos/estado-operativo"


class _Mensaje:
    def __init__(self, topic: str, datos: dict) -> None:
        self.topic = topic
        self.payload = json.dumps(datos).encode("utf-8")


class _Mudo:
    def __init__(self) -> None:
        self.eventos: list[tuple[dict, str]] = []

    async def publicar(self, evento, tipo="lectura"):
        self.eventos.append((evento, tipo))


class _Espia:
    def __init__(self) -> None:
        self.acks: list[dict] = []

    async def publish(self, topic, payload=None, qos=0, **_kw):
        self.acks.append(json.loads(payload))


@pytest_asyncio.fixture
async def nodo(db_session_factory, monkeypatch):
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    async with db_session_factory() as session:
        await SQLAlchemyDeviceRepository(session).obtener_o_crear(DEV)
        await session.commit()
    return db_session_factory


def _ahora_iso(delta: timedelta = timedelta()) -> str:
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _evento(tipo: str, detalle: dict, mudo: _Mudo | None = None, **extra) -> None:
    datos = {"schema_version": "1.0", "device_id": DEV, "event_type": tipo, "boot_id": 42,
             "time_quality": "sntp_synced", "occurred_at": _ahora_iso(), "uptime_s": 600, "detail": detalle, **extra}
    await main._procesar_evento_mqtt(_Mensaje(f"farmacias/{DEV}/eventos", datos), mudo or _Mudo())


async def _lectura(datos: dict) -> dict:
    espia = _Espia()
    await main._procesar_lectura_mqtt(_Mensaje(f"farmacias/{DEV}/lecturas", datos), _Mudo(), espia)
    return espia.acks[-1]


def _ejemplo(nombre: str, **cambios) -> dict:
    datos = json.loads((EJEMPLOS / f"{nombre}.json").read_text(encoding="utf-8"))
    if datos.get("captured_at"):
        datos["captured_at"] = _ahora_iso()
    return {**datos, **cambios}


def _estado(client, token) -> dict:
    respuesta = client.get(URL, params={"todos": "true"}, headers=auth_header(token))
    assert respuesta.status_code == 200
    return next(e for e in respuesta.json() if e["id"] == DEV)


# ── HU-48 ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_estado_nodo_con_pendientes_se_ve_en_el_estado_operativo(client, token_farmaceutico, nodo):
    """HU-48 esc. 1: número de registros retenidos y rango temporal que cubren."""
    ack = await _lectura(_ejemplo("telemetria_normal", seq_no=500))
    assert ack["status"] == "committed"
    mudo = _Mudo()
    await _evento("estado_nodo", {"pendientes": 240, "ocupacion_pct": 1, "capacidad": 30000, "perdidas_total": 0,
                                  "cuarentena": 0, "intervalo_s": 30}, mudo)

    buffer = _estado(client, token_farmaceutico)["buffer_littlefs"]

    assert (buffer["pendientes"], buffer["limite"], buffer["ocupacion_pct"]) == (240, 30000, 1)
    assert buffer["rango_estimado"] is True
    assert buffer["desde"] is not None and buffer["hasta"] is not None
    assert buffer["desde"] <= buffer["hasta"]
    assert (buffer["perdidas"], buffer["brechas"], buffer["ultima_sincronizacion_en"]) == (0, [], None)
    # El latido sigue llegando al tablero por SSE como antes.
    assert mudo.eventos[0][1] == "estado_nodo"


@pytest.mark.asyncio
async def test_sincronizacion_completa_quita_los_pendientes(client, token_tecnico, nodo):
    """HU-48 esc. 3: con 0 pendientes el indicador desaparece."""
    await _evento("estado_nodo", {"pendientes": 12, "capacidad": 30000,
                                  "pendientes_desde": _ahora_iso(-timedelta(minutes=6)),
                                  "pendientes_hasta": _ahora_iso()})
    con_pendientes = _estado(client, token_tecnico)["buffer_littlefs"]
    assert con_pendientes["rango_estimado"] is False and con_pendientes["pendientes"] == 12

    await _evento("estado_nodo", {"pendientes": 0, "capacidad": 30000})
    buffer = _estado(client, token_tecnico)["buffer_littlefs"]

    assert (buffer["pendientes"], buffer["desde"], buffer["hasta"]) == (0, None, None)
    assert buffer["ultima_sincronizacion_en"] is not None
    async with nodo() as session:
        assert len((await session.execute(select(EstadoNodoModel))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_buffer_saturado_muestra_la_brecha_y_su_causa(client, token_farmaceutico, nodo):
    """HU-48 esc. 2: al recuperar comunicación, la brecha temporal y su causa."""
    detalle = json.loads((EJEMPLOS / "evento_buffer_saturado.json").read_text(encoding="utf-8"))["detail"]
    await _evento("buffer_saturado", detalle)
    await _evento("buffer_saturado", {**detalle, "perdidas_episodio": 57, "episodio_abierto": False,
                                      "hasta_seq_no": 1356, "hasta": "2026-09-25T16:48:00Z", "perdidas_total": 57})
    await _evento("estado_nodo", {"pendientes": 0, "capacidad": 30000, "perdidas_total": 57})

    buffer = _estado(client, token_farmaceutico)["buffer_littlefs"]

    [brecha] = buffer["brechas"]
    assert (brecha["muestras_perdidas"], brecha["perdidas"], brecha["abierta"]) == (57, 57, False)
    assert (brecha["causa"], brecha["causa_nodo"]) == ("buffer_agotado", "buffer_lleno")
    assert "LittleFS" in brecha["descripcion"]
    assert brecha["desde"].startswith("2026-09-25T16:20:00") and brecha["hasta"].startswith("2026-09-25T16:48:00")
    assert (brecha["desde_seq_no"], brecha["hasta_seq_no"]) == (1300, 1356)
    assert buffer["perdidas"] == 57
    # Sigue auditándose como antes (HU-06 esc. 3).
    async with nodo() as session:
        acciones = [a.accion for a in (await session.execute(select(AuditLogModel))).scalars().all()]
        assert acciones.count("BUFFER_SATURADO") == 2
        assert len((await session.execute(select(EpisodioBufferSaturadoModel))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_estado_nodo_de_un_nodo_con_credencial_revocada_no_se_registra(client, token_tecnico, nodo):
    async with nodo() as session:
        dispositivos = SQLAlchemyDeviceRepository(session)
        await dispositivos.guardar_credencial(DEV, "hash-del-token", datetime.now(timezone.utc))
        await dispositivos.revocar_credencial(DEV, datetime.now(timezone.utc))
        await session.commit()
    await _evento("estado_nodo", {"pendientes": 5})
    async with nodo() as session:
        assert (await session.execute(select(EstadoNodoModel))).scalars().all() == []


@pytest.mark.asyncio
async def test_nodo_sin_estado_nodo_no_inventa_un_buffer(client, token_tecnico, nodo):
    assert _estado(client, token_tecnico)["buffer_littlefs"] is None


# ── HU-35 ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_el_estado_operativo_informa_el_umbral_de_puerta(client, token_tecnico, nodo):
    """El tablero dejaba de usar su propio valor (120 s) frente a los 60 s del backend."""
    estado = _estado(client, token_tecnico)
    assert estado["umbral_puerta_abierta_segundos"] == get_settings().puerta_umbral_prolongada_segundos == 60


# ── HU-07 ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lectura_sin_hora_se_rotula_hasta_que_el_arranque_sincroniza(client, token_tecnico, nodo):
    sin_hora = _ejemplo("telemetria_sin_hora", boot_id=77, seq_no=1, uptime_s=100)
    assert (await _lectura(sin_hora))["status"] == "committed"

    def _la_lectura() -> dict:
        lecturas = client.get("/api/lecturas", params={"device_id": DEV}, headers=auth_header(token_tecnico)).json()
        return next(lectura for lectura in lecturas if lectura["reading_id"] == f"{DEV}_77_1")

    antes = _la_lectura()
    assert (antes["sin_hora"], antes["origen_hora"], antes["time_quality"]) == (True, "recepcion", "unsynced")
    assert antes["recibida_en"] is not None

    real = datetime.now(timezone.utc).replace(microsecond=0)
    await _lectura(_ejemplo("telemetria_normal", boot_id=77, seq_no=2, uptime_s=160,
                            captured_at=real.strftime("%Y-%m-%dT%H:%M:%SZ")))

    despues = _la_lectura()
    assert (despues["sin_hora"], despues["origen_hora"]) == (False, "reconstruida")
    assert despues["timestamp_estimado"].startswith((real - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%S"))
