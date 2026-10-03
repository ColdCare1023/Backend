"""B-09: el tópico `farmacias/{device_id}/eventos` tiene manejador propio.

Antes, todo mensaje de ese tópico se validaba contra LecturaPayload, fallaba y
se descartaba en silencio: el Last Will and Testament que publica el broker
cuando el ESP32 se cae nunca llegaba a registrarse."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from src.infrastructure.database.repositories.audit_log_repository import (
    SQLAlchemyAuditLogRepository,
)
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.interface.api.sse_broadcaster import SSEBroadcaster
from src.interface.main import _procesar_mensaje_mqtt

DEVICE_ID = "ESP32-EV-01"


class _MensajeFalso:
    """Sustituto de aiomqtt.Message: solo se usan `topic` y `payload`."""

    def __init__(self, topic: str, payload: str) -> None:
        self.topic = topic
        self.payload = payload.encode("utf-8")


class _BroadcasterEspia(SSEBroadcaster):
    def __init__(self) -> None:
        super().__init__()
        self.publicados: list[tuple[dict, str]] = []

    async def publicar(self, evento: dict, tipo: str = "lectura") -> None:  # type: ignore[override]
        self.publicados.append((evento, tipo))


def _evento(tipo: str, **extra) -> str:
    import json

    base = {
        "device_id": DEVICE_ID,
        "tipo_evento": tipo,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }
    return json.dumps({**base, **extra})


@pytest_asyncio.fixture
async def sesion_con_device(db_session_factory, monkeypatch):
    """El manejador MQTT abre su propia sesión vía `_session_factory`; se
    apunta a la base en memoria de la prueba."""
    import src.interface.main as main

    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    async with db_session_factory() as session:
        await SQLAlchemyDeviceRepository(session).obtener_o_crear(DEVICE_ID)
        await session.commit()
    return db_session_factory


async def _auditoria(db_session_factory) -> list[dict]:
    async with db_session_factory() as session:
        return await SQLAlchemyAuditLogRepository(session).listar(limite=100)


async def _estado_device(db_session_factory, device_id: str = DEVICE_ID) -> dict:
    async with db_session_factory() as session:
        return await SQLAlchemyDeviceRepository(session).obtener(device_id)


@pytest.mark.asyncio
async def test_lwt_offline_marca_dispositivo_y_notifica(sesion_con_device):
    broadcaster = _BroadcasterEspia()
    mensaje = _MensajeFalso(f"farmacias/{DEVICE_ID}/eventos", _evento("lwt_offline"))

    await _procesar_mensaje_mqtt(mensaje, broadcaster)

    assert (await _estado_device(sesion_con_device))["estado_conectividad"] == "offline"
    assert [tipo for _, tipo in broadcaster.publicados] == ["desconexion"]
    acciones = [e["accion"] for e in await _auditoria(sesion_con_device)]
    assert "DISPOSITIVO_OFFLINE" in acciones


@pytest.mark.asyncio
async def test_lwt_online_restaura_estado(sesion_con_device):
    broadcaster = _BroadcasterEspia()
    await _procesar_mensaje_mqtt(
        _MensajeFalso(f"farmacias/{DEVICE_ID}/eventos", _evento("lwt_offline")), broadcaster
    )
    await _procesar_mensaje_mqtt(
        _MensajeFalso(f"farmacias/{DEVICE_ID}/eventos", _evento("lwt_online")), broadcaster
    )

    assert (await _estado_device(sesion_con_device))["estado_conectividad"] == "online"
    assert [tipo for _, tipo in broadcaster.publicados] == ["desconexion", "reconexion"]


@pytest.mark.asyncio
async def test_error_sensor_se_audita_con_detalle(sesion_con_device):
    broadcaster = _BroadcasterEspia()
    mensaje = _MensajeFalso(
        f"farmacias/{DEVICE_ID}/eventos",
        _evento("error_sensor", detalle="DS18B20 no responde en el bus 1-Wire"),
    )
    await _procesar_mensaje_mqtt(mensaje, broadcaster)

    assert [tipo for _, tipo in broadcaster.publicados] == ["fallo_sensor"]
    entradas = [e for e in await _auditoria(sesion_con_device) if e["accion"] == "ERROR_SENSOR"]
    assert len(entradas) == 1
    assert "DS18B20" in entradas[0]["detalle"]["detalle"]


@pytest.mark.asyncio
async def test_buffer_saturado_se_audita_con_periodo(sesion_con_device):
    """HU-06 criterio 3: la pérdida de lecturas por saturación del buffer
    offline no puede quedar silenciosa — debe auditarse con el periodo
    afectado que reporta el firmware en `detalle`."""
    broadcaster = _BroadcasterEspia()
    mensaje = _MensajeFalso(
        f"farmacias/{DEVICE_ID}/eventos",
        _evento(
            "buffer_saturado",
            detalle="12 lectura(s) descartada(s) por saturacion del buffer offline, "
            "periodo 2026-08-01T10:00:00Z a 2026-08-01T10:06:00Z",
        ),
    )
    await _procesar_mensaje_mqtt(mensaje, broadcaster)

    assert [tipo for _, tipo in broadcaster.publicados] == ["saturacion_buffer"]
    entradas = [e for e in await _auditoria(sesion_con_device) if e["accion"] == "BUFFER_SATURADO"]
    assert len(entradas) == 1
    assert "periodo 2026-08-01T10:00:00Z" in entradas[0]["detalle"]["detalle"]


@pytest.mark.asyncio
async def test_conmutacion_respaldo_se_audita_y_encadena_en_el_dispositivo(sesion_con_device):
    """HU-52 escenario 1: la conmutación a respaldo de 5V queda auditada y
    trazada en la MISMA cadena que el resto de eventos del dispositivo
    (HU-25) — no en la cadena de sistema."""
    broadcaster = _BroadcasterEspia()
    mensaje = _MensajeFalso(
        f"farmacias/{DEVICE_ID}/eventos",
        _evento("conmutacion_respaldo", detalle="Corte de suministro comercial detectado"),
    )
    await _procesar_mensaje_mqtt(mensaje, broadcaster)

    assert [tipo for _, tipo in broadcaster.publicados] == ["conmutacion_respaldo"]
    entradas = [e for e in await _auditoria(sesion_con_device) if e["accion"] == "CONMUTACION_RESPALDO"]
    assert len(entradas) == 1

    async with sesion_con_device() as session:
        trazabilidad = await SQLAlchemyTrazabilidadRepository(session).listar(chain_id=DEVICE_ID)
    assert any(r.tipo_evento == "AUDIT_LOG" for r in trazabilidad)


@pytest.mark.asyncio
async def test_respaldo_bajo_se_audita_como_diagnostico(sesion_con_device):
    """HU-52 escenario 3: el nivel de respaldo bajo umbral es un diagnóstico
    auditable, no una afirmación de que el nodo ya se apagó."""
    broadcaster = _BroadcasterEspia()
    mensaje = _MensajeFalso(
        f"farmacias/{DEVICE_ID}/eventos",
        _evento("respaldo_bajo", detalle="Nivel de respaldo bajo el umbral configurado"),
    )
    await _procesar_mensaje_mqtt(mensaje, broadcaster)

    assert [tipo for _, tipo in broadcaster.publicados] == ["respaldo_bajo"]
    entradas = [e for e in await _auditoria(sesion_con_device) if e["accion"] == "RESPALDO_BAJO"]
    assert len(entradas) == 1


@pytest.mark.asyncio
async def test_wifi_reconexion_prolongada_se_audita(sesion_con_device):
    """HU-08 criterio 3: superar el umbral operativo de reintentos Wi-Fi debe
    dejar una señal de diagnóstico observable — aquí, auditada al reconectar
    (es el primer momento en que el nodo vuelve a tener red para avisar)."""
    broadcaster = _BroadcasterEspia()
    mensaje = _MensajeFalso(
        f"farmacias/{DEVICE_ID}/eventos",
        _evento(
            "wifi_reconexion_prolongada",
            detalle="Reconectado tras 7 intento(s) fallido(s) (63 s sin red). "
            "Umbral de diagnostico: 5",
        ),
    )
    await _procesar_mensaje_mqtt(mensaje, broadcaster)

    assert [tipo for _, tipo in broadcaster.publicados] == ["reconexion_prolongada"]
    entradas = [
        e for e in await _auditoria(sesion_con_device) if e["accion"] == "WIFI_RECONEXION_PROLONGADA"
    ]
    assert len(entradas) == 1
    assert "7 intento(s)" in entradas[0]["detalle"]["detalle"]


@pytest.mark.asyncio
async def test_firmware_update_actualiza_version(sesion_con_device):
    broadcaster = _BroadcasterEspia()
    mensaje = _MensajeFalso(
        f"farmacias/{DEVICE_ID}/eventos", _evento("firmware_update", firmware_version="1.4.2")
    )
    await _procesar_mensaje_mqtt(mensaje, broadcaster)

    assert (await _estado_device(sesion_con_device))["firmware_version"] == "1.4.2"
    acciones = [e["accion"] for e in await _auditoria(sesion_con_device)]
    assert "FIRMWARE_ACTUALIZADO" in acciones


@pytest.mark.asyncio
async def test_device_id_que_no_coincide_con_el_topico_se_descarta(sesion_con_device):
    """Anti-suplantación: si no se validara, un nodo podría declarar offline
    a otro publicando en su propio tópico."""
    broadcaster = _BroadcasterEspia()
    # Se deja el nodo en línea para que un "offline" suplantado sea detectable:
    # el estado por defecto de un dispositivo recién creado ya es "offline".
    await _procesar_mensaje_mqtt(
        _MensajeFalso(f"farmacias/{DEVICE_ID}/eventos", _evento("lwt_online")), broadcaster
    )
    assert (await _estado_device(sesion_con_device))["estado_conectividad"] == "online"
    broadcaster.publicados.clear()

    # Cuerpo que declara ser ESP32-EV-01 pero publicado en el tópico de otro nodo.
    await _procesar_mensaje_mqtt(
        _MensajeFalso("farmacias/OTRO-NODO/eventos", _evento("lwt_offline")), broadcaster
    )

    assert broadcaster.publicados == []
    assert (await _estado_device(sesion_con_device))["estado_conectividad"] == "online"


@pytest.mark.asyncio
async def test_evento_de_dispositivo_no_provisionado_se_audita_y_descarta(sesion_con_device):
    """Un evento no debe dar de alta un dispositivo por la puerta de atrás."""
    broadcaster = _BroadcasterEspia()
    import json

    cuerpo = json.dumps(
        {
            "device_id": "INTRUSO-99",
            "tipo_evento": "lwt_online",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }
    )
    await _procesar_mensaje_mqtt(_MensajeFalso("farmacias/INTRUSO-99/eventos", cuerpo), broadcaster)

    assert broadcaster.publicados == []
    assert await _estado_device(sesion_con_device, "INTRUSO-99") is None
    acciones = [e["accion"] for e in await _auditoria(sesion_con_device)]
    assert "EVENTO_DISPOSITIVO_DESCONOCIDO" in acciones


@pytest.mark.asyncio
async def test_evento_con_json_invalido_no_rompe_el_consumidor(sesion_con_device):
    broadcaster = _BroadcasterEspia()
    await _procesar_mensaje_mqtt(
        _MensajeFalso(f"farmacias/{DEVICE_ID}/eventos", '{"tipo_evento": "no_existe"}'), broadcaster
    )
    assert broadcaster.publicados == []


@pytest.mark.asyncio
async def test_las_lecturas_siguen_llegando_a_su_manejador(sesion_con_device):
    """El despacho por tópico no debe haber roto el flujo de lecturas."""
    import json

    broadcaster = _BroadcasterEspia()
    cuerpo = json.dumps(
        {
            "device_id": DEVICE_ID,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "temperatura_ambiental": 21.0,
            "humedad_ambiental": 55.0,
            "temperatura_interna": 5.0,
            "apertura_refrigerador": False,
            "estado_conectividad": "online",
        }
    )
    await _procesar_mensaje_mqtt(_MensajeFalso(f"farmacias/{DEVICE_ID}/lecturas", cuerpo), broadcaster)

    # HU-17: sin historial previo (dispositivo recién conectado) no hay forma
    # de calcular una tendencia térmica real; la lectura se persiste pero
    # queda no_clasificable en vez de imputar una tendencia de 0.0 — por eso
    # el tipo de evento SSE es "inferencia_omitida", no "lectura". El punto
    # de esta prueba (que el despacho por tópico siga entregando lecturas al
    # manejador) sigue cumplido: el evento llegó.
    assert [tipo for _, tipo in broadcaster.publicados] == ["inferencia_omitida"]
