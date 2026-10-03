"""HU-05 (schema_version + reading_id explícitos en el payload) y HU-07
(acuse lógico de aplicación publicado por el backend tras el COMMIT, no solo
el PUBACK de transporte) del backlog de 51 HU finales."""

import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.mqtt.payload_schema import LecturaPayload
from src.interface import main

DEVICE_ID = "FARM-HU07-01"
TOPIC = f"farmacias/{DEVICE_ID}/lecturas"


class _MensajeFalso:
    def __init__(self, topic: str, payload: str) -> None:
        self.topic = topic
        self.payload = payload.encode("utf-8")


class _ClienteEspia:
    """Doble mínimo de aiomqtt.Client — solo lo que _publicar_ack_lectura usa."""

    def __init__(self) -> None:
        self.publicados: list[tuple[str, dict, int]] = []

    async def publish(self, topic, payload=None, qos=0, **_kwargs):
        self.publicados.append((topic, json.loads(payload), qos))


def _payload(**overrides) -> str:
    base = {
        "device_id": DEVICE_ID,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "schema_version": 1,
        "reading_id": "esp32-0001",
        "temperatura_interna": 5.0,
        "temperatura_ambiental": 21.0,
        "humedad_ambiental": 55.0,
        "apertura_refrigerador": False,
        "estado_conectividad": "online",
    }
    base.update(overrides)
    return json.dumps(base)


@pytest_asyncio.fixture
async def entorno(db_session_factory, monkeypatch):
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    async with db_session_factory() as session:
        await SQLAlchemyDeviceRepository(session).obtener_o_crear(DEVICE_ID)
        await session.commit()
    return db_session_factory


class _BroadcasterMudo:
    async def publicar(self, evento, tipo="lectura"):
        return None


# ── HU-05: contrato del payload ───────────────────────────────────────────


def test_schema_version_no_soportada_se_rechaza():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LecturaPayload.model_validate_json(_payload(schema_version=99))


def test_schema_version_por_defecto_es_1_para_firmware_desactualizado():
    """Compatibilidad hacia atrás: firmware que aún no declara schema_version
    no debe romperse — usa el valor por defecto."""
    cuerpo = json.loads(_payload())
    del cuerpo["schema_version"]
    payload = LecturaPayload.model_validate_json(json.dumps(cuerpo))
    assert payload.schema_version == 1


def test_reading_id_es_opcional_para_compatibilidad():
    cuerpo = json.loads(_payload())
    del cuerpo["reading_id"]
    payload = LecturaPayload.model_validate_json(json.dumps(cuerpo))
    assert payload.reading_id is None


@pytest.mark.asyncio
async def test_reading_id_y_schema_version_se_persisten(entorno):
    from src.infrastructure.database.repositories.lectura_repository import (
        SQLAlchemyLecturaRepository,
    )

    mensaje = _MensajeFalso(TOPIC, _payload(reading_id="esp32-persist-01"))
    await main._procesar_lectura_mqtt(mensaje, _BroadcasterMudo(), None)

    async with entorno() as session:
        lecturas = await SQLAlchemyLecturaRepository(session).listar(device_id=DEVICE_ID, limite=1)

    assert lecturas[0].reading_id == "esp32-persist-01"
    assert lecturas[0].schema_version == 1


# ── HU-07: acuse lógico tras el COMMIT ────────────────────────────────────


@pytest.mark.asyncio
async def test_backend_publica_ack_solo_despues_del_commit(entorno):
    """El acuse debe llegar al tópico farmacias/{device_id}/ack, con QoS 1,
    referenciando el mismo reading_id — nunca antes de que la lectura esté
    confirmada en PostgreSQL."""
    cliente = _ClienteEspia()
    mensaje = _MensajeFalso(TOPIC, _payload(reading_id="esp32-0042"))

    await main._procesar_lectura_mqtt(mensaje, _BroadcasterMudo(), cliente)

    assert len(cliente.publicados) == 1
    topico, cuerpo, qos = cliente.publicados[0]
    assert topico == f"farmacias/{DEVICE_ID}/ack"
    assert qos == 1
    assert cuerpo["device_id"] == DEVICE_ID
    assert cuerpo["reading_id"] == "esp32-0042"
    assert cuerpo["estado"] == "commit_confirmado"


@pytest.mark.asyncio
async def test_sin_cliente_no_falla_el_pipeline(entorno):
    """Pruebas/entornos que invocan el manejador directamente sin un broker
    real (client=None) no deben romperse: el acuse simplemente se omite."""
    mensaje = _MensajeFalso(TOPIC, _payload(reading_id="esp32-0043"))

    # No debe lanzar excepción alguna.
    await main._procesar_lectura_mqtt(mensaje, _BroadcasterMudo(), None)


@pytest.mark.asyncio
async def test_reenvio_de_lectura_ya_confirmada_tambien_recibe_ack(entorno):
    """HU-07 Escenario 2: si el ESP32 reenvía un bloque cuyo COMMIT ya
    ocurrió (el acuse anterior se perdió), el backend deduplica pero SIGUE
    confirmando — si no, el nodo reintentaría ese bloque para siempre."""
    cliente = _ClienteEspia()
    cuerpo = _payload(reading_id="esp32-0044")

    await main._procesar_lectura_mqtt(_MensajeFalso(TOPIC, cuerpo), _BroadcasterMudo(), cliente)
    assert len(cliente.publicados) == 1

    # Mismo device_id + mismo timestamp exacto = reenvío duplicado.
    await main._procesar_lectura_mqtt(_MensajeFalso(TOPIC, cuerpo), _BroadcasterMudo(), cliente)
    assert len(cliente.publicados) == 2
    assert cliente.publicados[1][1]["reading_id"] == "esp32-0044"


@pytest.mark.asyncio
async def test_lectura_invalida_tambien_recibe_ack(entorno):
    """HU-07: un rechazo PERMANENTE (timestamp implausible, B-10) también debe
    confirmarse. Sin esto, el firmware reintentaría este bloque para siempre y
    bloquearía toda la cola FIFO detrás de él (core::drenar se detiene en el
    primer fallo)."""
    cliente = _ClienteEspia()
    timestamp_futuro = datetime(2099, 1, 1, tzinfo=timezone.utc).isoformat()
    mensaje = _MensajeFalso(
        TOPIC, _payload(reading_id="esp32-invalida-01", timestamp=timestamp_futuro)
    )

    await main._procesar_lectura_mqtt(mensaje, _BroadcasterMudo(), cliente)

    assert len(cliente.publicados) == 1
    topico, cuerpo, qos = cliente.publicados[0]
    assert topico == f"farmacias/{DEVICE_ID}/ack"
    assert qos == 1
    assert cuerpo["reading_id"] == "esp32-invalida-01"
    assert cuerpo["estado"] == "lectura_invalida"


@pytest.mark.asyncio
async def test_dispositivo_no_autorizado_tambien_recibe_ack(entorno, monkeypatch):
    """Mismo razonamiento que arriba, para el otro rechazo permanente: un
    dispositivo no registrado bajo `device_registry_estricto=True`.

    `conftest.py` fija `DEVICE_REGISTRY_ESTRICTO=false` como default de todo
    el entorno de pruebas (para no tener que registrar un dispositivo en cada
    test que no es sobre esto); aquí se fuerza el modo estricto solo para
    este test, igual que hace `test_seguridad_api.py`."""
    from src.infrastructure.config import get_settings

    settings_estrictos = get_settings().model_copy(update={"device_registry_estricto": True})
    monkeypatch.setattr(main, "get_settings", lambda: settings_estrictos)

    cliente = _ClienteEspia()
    device_desconocido = "FARM-NO-REGISTRADO"
    topico = f"farmacias/{device_desconocido}/lecturas"
    mensaje = _MensajeFalso(
        topico,
        _payload(device_id=device_desconocido, reading_id="esp32-no-autorizado-01"),
    )

    await main._procesar_lectura_mqtt(mensaje, _BroadcasterMudo(), cliente)

    assert len(cliente.publicados) == 1
    topico_ack, cuerpo, qos = cliente.publicados[0]
    assert topico_ack == f"farmacias/{device_desconocido}/ack"
    assert qos == 1
    assert cuerpo["reading_id"] == "esp32-no-autorizado-01"
    assert cuerpo["estado"] == "dispositivo_no_autorizado"


# ── HU-07: el firmware parsea el acuse con búsqueda de texto plano ────────


async def test_acuse_usa_json_compacto_para_el_parser_del_firmware():
    """core::extraerCampoString busca `"reading_id":"<valor>"` sin espacios; con el
    formato por defecto de json.dumps (`": "`) el nodo nunca reconocía su acuse."""
    crudos: list[str] = []

    class _Crudo:
        async def publish(self, topic, payload=None, qos=0, **_kw):
            crudos.append(payload)

    await main._publicar_ack_lectura(_Crudo(), DEVICE_ID, "esp32-0001", datetime.now(tz=timezone.utc))

    assert '"reading_id":"esp32-0001"' in crudos[0]


# ── HU-11: conflicto de payload ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_mismo_timestamp_con_otra_identidad_se_guardan_ambas(entorno):
    """Reloj sin NTP: tras reiniciar, el nodo repite timestamps con otro boot_id.
    Son lecturas distintas (la identidad es boot_id+seq_no): se guardan las dos."""
    cliente = _ClienteEspia()
    ts = datetime(2026, 10, 2, 19, 30, 9, tzinfo=timezone.utc).isoformat()
    primera = _payload(reading_id="a", timestamp=ts, boot_id=22, seq_no=0, temperatura_interna=5.0)
    segunda = _payload(reading_id="b", timestamp=ts, boot_id=2, seq_no=0, temperatura_interna=6.5)

    await main._procesar_lectura_mqtt(_MensajeFalso(TOPIC, primera), _BroadcasterMudo(), cliente)
    await main._procesar_lectura_mqtt(_MensajeFalso(TOPIC, segunda), _BroadcasterMudo(), cliente)

    assert cliente.publicados[0][1]["estado"] == "commit_confirmado"
    assert cliente.publicados[1][1]["estado"] == "commit_confirmado"


@pytest.mark.asyncio
async def test_misma_identidad_con_valores_distintos_es_conflicto(entorno):
    cliente = _ClienteEspia()
    ts = datetime(2026, 10, 2, 19, 31, 9, tzinfo=timezone.utc).isoformat()
    original = _payload(reading_id="c", timestamp=ts, boot_id=5, seq_no=1, temperatura_interna=5.0)
    alterada = _payload(reading_id="c", timestamp=ts, boot_id=5, seq_no=1, temperatura_interna=9.0)

    await main._procesar_lectura_mqtt(_MensajeFalso(TOPIC, original), _BroadcasterMudo(), cliente)
    await main._procesar_lectura_mqtt(_MensajeFalso(TOPIC, alterada), _BroadcasterMudo(), cliente)

    assert cliente.publicados[1][1]["estado"] == "conflicto_payload"


@pytest.mark.asyncio
async def test_reenvio_identico_no_es_conflicto(entorno):
    cliente = _ClienteEspia()
    cuerpo = _payload(reading_id="d", boot_id=7, seq_no=3)

    await main._procesar_lectura_mqtt(_MensajeFalso(TOPIC, cuerpo), _BroadcasterMudo(), cliente)
    await main._procesar_lectura_mqtt(_MensajeFalso(TOPIC, cuerpo), _BroadcasterMudo(), cliente)

    assert cliente.publicados[1][1]["estado"] == "commit_confirmado"


# ── HU-15: rechazo por esquema ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_payload_fuera_de_contrato_se_acusa_y_no_se_persiste(entorno):
    """Humedad fuera de 0-100 %: se rechaza sin excepción y con acuse, para que
    el nodo no reintente ese archivo indefinidamente."""
    cliente = _ClienteEspia()
    cuerpo = _payload(reading_id="esp32-humedad-120", humedad_ambiental=120.0)

    await main._procesar_lectura_mqtt(_MensajeFalso(TOPIC, cuerpo), _BroadcasterMudo(), cliente)

    assert cliente.publicados[0][1]["estado"] == "payload_rechazado"
    assert cliente.publicados[0][1]["reading_id"] == "esp32-humedad-120"


@pytest.mark.asyncio
async def test_json_malformado_se_descarta_sin_excepcion(entorno):
    cliente = _ClienteEspia()

    await main._procesar_lectura_mqtt(_MensajeFalso(TOPIC, '{"device_id": "x",'), _BroadcasterMudo(), cliente)

    assert cliente.publicados == []


# ── HU-04: nodo sin MC-38 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nodo_sin_mc38_persiste_puerta_como_null(entorno):
    from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository

    cliente = _ClienteEspia()
    ts = datetime(2026, 10, 3, 1, 53, 6, tzinfo=timezone.utc)
    cuerpo = _payload(reading_id="sin-mc38", timestamp=ts.isoformat(), apertura_refrigerador=None,
                      mc38_status="not_installed")

    await main._procesar_lectura_mqtt(_MensajeFalso(TOPIC, cuerpo), _BroadcasterMudo(), cliente)

    async with entorno() as session:
        guardada = await SQLAlchemyLecturaRepository(session).obtener_por_device_y_timestamp(DEVICE_ID, ts)
    assert guardada.apertura_refrigerador is None
