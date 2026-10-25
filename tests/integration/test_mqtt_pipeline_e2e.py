"""Camino completo de ingesta MQTT con un broker simulado (RF-04..RF-11).

Por qué existe este archivo: las pruebas de contrato previas
(`test_contrato_firmware_esp32.py`) llamaban directamente a
`_procesar_mensaje_mqtt`, saltándose por completo `infrastructure/mqtt/mqtt_client.py`.
Por eso el defecto S-01 —el constructor de aiomqtt invocado con los nombres de
la versión 1.x— sobrevivió meses con la suite en verde: nada ejercitaba el
módulo que conecta con el broker.

Aquí los mensajes entran por donde entran en producción: `consumir_mensajes` /
`consumir_con_reconexion` sobre un cliente que imita la interfaz real de
aiomqtt 2.x (`messages` como propiedad, context manager asíncrono), y recorren
payload del ESP32 → validación Pydantic → clasificación IA → persistencia →
alerta → evento SSE.
"""

import asyncio
import inspect
import json
from datetime import datetime, timedelta, timezone

import aiomqtt
import pytest
import pytest_asyncio

from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.infrastructure.mqtt import mqtt_client
from src.interface.api.sse_broadcaster import SSEBroadcaster

DEVICE_ID = "FARM-01-CDL"
TOPIC = f"farmacias/{DEVICE_ID}/lecturas"


# ── Doble del broker ──────────────────────────────────────────────────────


class _MensajeFalso:
    def __init__(self, topic: str, payload: bytes | str) -> None:
        self.topic = topic
        self.payload = payload.encode("utf-8") if isinstance(payload, str) else payload


class _BrokerFalso:
    """Imita la superficie de `aiomqtt.Client` que usa `_sesion_una_vez`.

    Deliberadamente reproduce la forma de aiomqtt 2.x: `messages` es una
    PROPIEDAD (no un método) y la clase es un context manager asíncrono. Si una
    futura actualización de aiomqtt cambia ese contrato, esta prueba deja de
    parecerse a la realidad — por eso se acompaña de
    `test_los_parametros_de_build_client_existen_en_la_version_instalada`, que
    contrasta contra la firma real de la librería instalada.
    """

    def __init__(self, mensajes, error_al_terminar: Exception | None = None) -> None:
        self._mensajes = list(mensajes)
        self._error = error_al_terminar
        self.suscripciones: list[str] = []
        self.qos_suscripciones: list[int] = []
        self.conexiones = 0
        # HU-07: acuses lógicos que el backend publicó de vuelta al firmware.
        self.acks_publicados: list[tuple[str, str]] = []

    async def __aenter__(self):
        self.conexiones += 1
        return self

    async def __aexit__(self, *exc):
        return False

    async def subscribe(self, topic, qos=0):
        self.suscripciones.append(topic)
        self.qos_suscripciones.append(qos)
        return (qos,)  # QoS concedido, como el SUBACK real de aiomqtt

    async def publish(self, topic, payload=None, qos=0, **_kwargs):
        self.acks_publicados.append((topic, payload))

    @property
    def messages(self):
        async def generador():
            for mensaje in self._mensajes:
                yield mensaje
            if self._error is not None:
                raise self._error

        return generador()


class _BroadcasterEspia(SSEBroadcaster):
    def __init__(self) -> None:
        super().__init__()
        self.publicados: list[tuple[dict, str]] = []

    async def publicar(self, evento: dict, tipo: str = "lectura") -> None:  # type: ignore[override]
        self.publicados.append((evento, tipo))


def _payload_firmware(**overrides) -> str:
    """Réplica byte a byte de `core::serializarLectura()`: mismas claves, mismo
    orden y los tres campos de sensor siempre presentes (`null` si el sensor
    falló, nunca 0.0)."""
    base = {
        "device_id": DEVICE_ID,
        "timestamp": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "estado_conectividad": "online",
        "firmware_version": "1.4.0",
        "temperatura_interna": 4.53,
        "temperatura_ambiental": 5.21,
        "humedad_ambiental": 62.40,
        "apertura_refrigerador": False,
        "duracion_apertura_segundos": 0,
    }
    base.update(overrides)
    return json.dumps(base)


@pytest_asyncio.fixture
async def entorno(db_session_factory, monkeypatch):
    """Sesión de BD aislada y dispositivo provisionado."""
    import src.interface.main as main

    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    async with db_session_factory() as session:
        await SQLAlchemyDeviceRepository(session).obtener_o_crear(DEVICE_ID)
        await session.commit()
    return db_session_factory


async def _consumir(mensajes, broadcaster) -> _BrokerFalso:
    """Entrega los mensajes por el mismo camino que en producción."""
    import src.interface.main as main

    broker = _BrokerFalso(mensajes)

    async def manejador(cliente, mensaje):
        await main._procesar_mensaje_mqtt(mensaje, broadcaster, cliente)

    await mqtt_client.consumir_mensajes(broker, manejador)
    return broker


async def _lecturas(factory):
    async with factory() as session:
        return await SQLAlchemyLecturaRepository(session).listar(device_id=DEVICE_ID, limite=50)


async def _alertas(factory):
    async with factory() as session:
        return await SQLAlchemyAlertaRepository(session).listar(device_id=DEVICE_ID, limite=50)


# ── Camino completo ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lectura_normal_recorre_todo_el_camino(entorno):
    """ESP32 → broker → validación → IA → persistencia → SSE."""
    broadcaster_previa = _BroadcasterEspia()
    # HU-17: la IA necesita 2+ lecturas previas válidas para tendencia.
    ahora = datetime.now(tz=timezone.utc)
    await _consumir(
        [
            _MensajeFalso(
                TOPIC,
                _payload_firmware(
                    timestamp=(ahora - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
                ),
            ),
            _MensajeFalso(
                TOPIC,
                _payload_firmware(
                    timestamp=(ahora - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
                ),
            ),
        ],
        broadcaster_previa,
    )

    broadcaster = _BroadcasterEspia()
    await _consumir([_MensajeFalso(TOPIC, _payload_firmware())], broadcaster)

    lecturas = await _lecturas(entorno)
    assert len(lecturas) == 3
    assert lecturas[0].temperatura_interna == pytest.approx(4.53)
    # La clasificación se ejecutó de verdad: hay veredicto y evidencia de IA.
    assert lecturas[0].nivel_riesgo is not None
    assert lecturas[0].modelo_version is not None
    assert [tipo for _, tipo in broadcaster.publicados] == ["lectura"]


@pytest.mark.asyncio
async def test_duracion_apertura_llega_al_evento_sse(entorno):
    """HU-35: el frontend necesita `duracion_apertura_segundos` para avisar
    de una puerta abierta más allá del umbral — antes se guardaba en el
    `payload` JSONB pero nunca salía de ahí hacia la API/SSE."""
    broadcaster = _BroadcasterEspia()
    await _consumir(
        [
            _MensajeFalso(
                TOPIC,
                _payload_firmware(apertura_refrigerador=True, duracion_apertura_segundos=185),
            )
        ],
        broadcaster,
    )

    # HU-13: el segundo evento es el cambio de conectividad por telemetría.
    assert [tipo for _, tipo in broadcaster.publicados][1:] == ["conectividad"]
    evento, _tipo = broadcaster.publicados[0]
    assert evento["apertura_refrigerador"] is True
    assert evento["duracion_apertura_segundos"] == 185


@pytest.mark.asyncio
async def test_excursion_critica_genera_alerta_y_evento_sse(entorno):
    """RF-08/RF-09/RF-11: 19.9 °C es una excursión inequívoca."""
    broadcaster_previa = _BroadcasterEspia()
    # HU-17: la IA necesita 2+ lecturas previas válidas para tendencia (el
    # que la excursión SIGA detectándose sin esto ya lo cubre HU-21 en otra
    # prueba; aquí se conserva el escenario original con IA completada).
    ahora = datetime.now(tz=timezone.utc)
    await _consumir(
        [
            _MensajeFalso(
                TOPIC,
                _payload_firmware(
                    timestamp=(ahora - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
                ),
            ),
            _MensajeFalso(
                TOPIC,
                _payload_firmware(
                    timestamp=(ahora - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
                ),
            ),
        ],
        broadcaster_previa,
    )

    broadcaster = _BroadcasterEspia()

    await _consumir(
        [_MensajeFalso(TOPIC, _payload_firmware(temperatura_interna=19.9, temperatura_ambiental=21.5))],
        broadcaster,
    )

    alertas = await _alertas(entorno)
    assert len(alertas) == 1
    assert alertas[0].nivel_riesgo.value == "excursion_critica"

    tipos = [tipo for _, tipo in broadcaster.publicados]
    assert "lectura" in tipos
    assert "alerta" in tipos


@pytest.mark.asyncio
async def test_la_lectura_queda_encadenada_en_la_trazabilidad(entorno):
    """RF-14: cada lectura ingresada por MQTT debe dejar su eslabón SHA-256."""
    broadcaster = _BroadcasterEspia()

    await _consumir([_MensajeFalso(TOPIC, _payload_firmware())], broadcaster)

    async with entorno() as session:
        registros = await SQLAlchemyTrazabilidadRepository(session).listar_todos_ordenados()

    assert len(registros) >= 1
    assert registros[-1].hash_encadenado.hash_actual != ""


@pytest.mark.asyncio
async def test_una_rafaga_de_lecturas_se_procesa_en_orden(entorno):
    """El buffer LittleFS del nodo (RF-06) vuelca varias lecturas seguidas al
    reconectar; todas deben persistirse, no solo la primera."""
    broadcaster = _BroadcasterEspia()
    base = datetime.now(tz=timezone.utc).replace(microsecond=0, second=0)
    # Timestamps distintos: la deduplicación (RF-07) descarta el mismo instante,
    # que es justo lo que ocurre si el nodo reenvía por QoS1.
    mensajes = [
        _MensajeFalso(
            TOPIC,
            _payload_firmware(
                timestamp=base.strftime(f"%Y-%m-%dT%H:%M:{i:02d}Z"),
                temperatura_interna=4.0 + i * 0.1,
            ),
        )
        for i in range(5)
    ]

    await _consumir(mensajes, broadcaster)

    assert len(await _lecturas(entorno)) == 5


# ── Robustez ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_payload_malformado_no_detiene_el_consumo(entorno):
    """Un JSON roto entre dos lecturas buenas: se descarta y el consumidor
    sigue. Si propagara, una sola trama corrupta del nodo dejaría la farmacia
    sin vigilancia hasta el siguiente reinicio."""
    broadcaster = _BroadcasterEspia()
    ahora = datetime.now(tz=timezone.utc).replace(microsecond=0)

    mensajes = [
        _MensajeFalso(TOPIC, _payload_firmware(timestamp=ahora.strftime("%Y-%m-%dT%H:%M:01Z"))),
        _MensajeFalso(TOPIC, '{"device_id": "FARM-01-CDL", "timestamp": '),  # JSON truncado
        _MensajeFalso(TOPIC, "no es json en absoluto"),
        _MensajeFalso(TOPIC, _payload_firmware(timestamp=ahora.strftime("%Y-%m-%dT%H:%M:02Z"))),
    ]

    await _consumir(mensajes, broadcaster)

    assert len(await _lecturas(entorno)) == 2, "las dos lecturas válidas deben persistirse"


@pytest.mark.asyncio
async def test_payload_con_campo_desconocido_se_descarta_sin_romper(entorno):
    """El contrato es cerrado (`extra="forbid"`): un campo de más indica
    firmware desalineado, y debe descartarse el mensaje, no el consumidor."""
    broadcaster = _BroadcasterEspia()

    await _consumir(
        [_MensajeFalso(TOPIC, _payload_firmware(campo_que_no_existe=1))], broadcaster
    )

    assert await _lecturas(entorno) == []


@pytest.mark.asyncio
async def test_payload_desmesurado_se_rechaza_antes_de_deserializar(entorno):
    """El firmware corta en 512 bytes. La ingesta MQTT no pasa por el
    middleware que acota el cuerpo REST, así que sin este techo un publicador
    con credenciales del broker podía forzar la materialización de un mensaje
    enorme en una instancia de 512 MB."""
    broadcaster = _BroadcasterEspia()
    enorme = _payload_firmware(firmware_version="1.4.0")
    relleno = json.dumps({"device_id": DEVICE_ID, "basura": "x" * 100_000})

    await _consumir([_MensajeFalso(TOPIC, relleno), _MensajeFalso(TOPIC, enorme)], broadcaster)

    assert len(await _lecturas(entorno)) == 1, "solo la lectura legítima debe entrar"


@pytest.mark.asyncio
async def test_device_id_que_no_coincide_con_el_topico_se_descarta(entorno):
    """Anti-suplantación: el broker autoriza a publicar en el tópico de UN
    dispositivo; el cuerpo no puede declarar ser otro."""
    broadcaster = _BroadcasterEspia()

    await _consumir(
        [_MensajeFalso("farmacias/OTRO-NODO/lecturas", _payload_firmware())], broadcaster
    )

    assert await _lecturas(entorno) == []


@pytest.mark.asyncio
async def test_la_ingesta_se_reanuda_tras_una_caida_del_broker(monkeypatch):
    """S-02 end-to-end: el broker corta la conexión tras entregar la primera
    lectura; al reconectar entrega la segunda, que también debe procesarse.

    Se afirma sobre los mensajes ENTREGADOS al manejador y no sobre filas en la
    base de datos: la SQLite en memoria de las pruebas vive en una única
    conexión compartida (`StaticPool`) y no admite que una tarea de fondo la
    use en paralelo con el hilo de la prueba. La persistencia del camino
    completo ya la cubren las pruebas anteriores de este archivo; lo que aquí
    hay que fijar es que la reconexión ocurre y el flujo se reanuda."""
    recibidos: list[str] = []
    sesiones = [
        _BrokerFalso(
            [_MensajeFalso(TOPIC, _payload_firmware(temperatura_interna=4.0))],
            error_al_terminar=aiomqtt.MqttError("conexión perdida"),
        ),
        _BrokerFalso([_MensajeFalso(TOPIC, _payload_firmware(temperatura_interna=6.0))]),
    ]
    creados = []

    def build_client_falso(settings):
        cliente = sesiones[min(len(creados), len(sesiones) - 1)]
        creados.append(cliente)
        return cliente

    monkeypatch.setattr(mqtt_client, "build_client", build_client_falso)
    monkeypatch.setattr(mqtt_client, "RECONEXION_ESPERA_INICIAL_SEGUNDOS", 0.001)
    monkeypatch.setattr(mqtt_client, "RECONEXION_ESPERA_MAXIMA_SEGUNDOS", 0.001)

    async def manejador(cliente, mensaje):
        recibidos.append(json.loads(mensaje.payload)["temperatura_interna"])

    from src.infrastructure.config import get_settings

    tarea = asyncio.create_task(mqtt_client.consumir_con_reconexion(get_settings(), manejador))
    await asyncio.sleep(0.1)
    tarea.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarea

    assert len(creados) >= 2, "debe haberse reconectado tras la caída"
    assert 4.0 in recibidos, "la lectura previa a la caída debe haberse procesado"
    assert 6.0 in recibidos, "la ingesta debe reanudarse tras reconectar"


@pytest.mark.asyncio
async def test_al_conectar_se_suscribe_a_lecturas_y_a_eventos(monkeypatch):
    """Olvidar una suscripción no rompe nada visible: simplemente ese flujo
    deja de llegar. Los eventos LWT del nodo (B-09) son justo eso."""
    broker = _BrokerFalso([])
    monkeypatch.setattr(mqtt_client, "build_client", lambda settings: broker)

    from src.infrastructure.config import get_settings

    async def manejador(cliente, mensaje):  # pragma: no cover
        return None

    await mqtt_client._sesion_una_vez(get_settings(), manejador)

    assert set(broker.suscripciones) == {
        mqtt_client.TOPIC_LECTURAS,
        mqtt_client.TOPIC_EVENTOS,
    }
    # HU-11: QoS 1 también en el tramo broker→backend.
    assert broker.qos_suscripciones == [1, 1]


# ── Guardia contra la reaparición de S-01 ─────────────────────────────────


def test_los_parametros_de_build_client_existen_en_la_version_instalada():
    """S-01 fue exactamente esto: `build_client` llamaba a `aiomqtt.Client` con
    `ssl_context`, `reconnect` y `keep_alive`, que son nombres de aiomqtt 1.x.
    El doble de broker de este archivo no puede detectarlo (imita la interfaz,
    no la valida), así que aquí se contrasta contra la firma REAL de la
    librería instalada. Si una futura actualización renombra un parámetro, esta
    prueba falla en CI en vez de en el despliegue."""
    firma = inspect.signature(aiomqtt.Client.__init__)
    usados = {
        "hostname",
        "port",
        "username",
        "password",
        "identifier",
        "tls_context",
        "keepalive",
        "clean_session",
    }

    faltantes = usados - set(firma.parameters)
    assert not faltantes, f"aiomqtt ya no acepta: {sorted(faltantes)}"


def test_el_backend_usa_sesion_mqtt_persistente(monkeypatch):
    """HU-13 criterio 5: con sesión limpia, un LWT publicado con el backend
    caído se perdía y el nodo apagado seguía «online»."""
    from src.infrastructure.config import get_settings

    capturados = {}
    monkeypatch.setattr(mqtt_client.aiomqtt, "Client", lambda **kw: capturados.update(kw))
    mqtt_client.build_client(get_settings())
    assert capturados["clean_session"] is False
    assert capturados["identifier"]  # la sesión persistente exige client_id fijo


# ── Rechazos que deben quedar auditados ───────────────────────────────────


@pytest.mark.asyncio
async def test_dispositivo_no_provisionado_se_rechaza_y_se_audita(entorno, monkeypatch):
    """Con registro estricto, un nodo que no está en `devices` no puede
    empezar a inyectar lecturas. El rechazo debe quedar en `audit_logs`: si
    solo fuera al log, un intento de suplantación no dejaría evidencia."""
    from src.infrastructure.config import Settings, get_settings
    from src.infrastructure.database.repositories.audit_log_repository import (
        SQLAlchemyAuditLogRepository,
    )

    get_settings.cache_clear()
    monkeypatch.setenv("DEVICE_REGISTRY_ESTRICTO", "true")
    try:
        assert Settings().device_registry_estricto is True
        broadcaster = _BroadcasterEspia()
        intruso = "NODO-NO-PROVISIONADO"

        await _consumir(
            [
                _MensajeFalso(
                    f"farmacias/{intruso}/lecturas",
                    _payload_firmware().replace(DEVICE_ID, intruso),
                )
            ],
            broadcaster,
        )

        async with entorno() as session:
            registros = await SQLAlchemyAuditLogRepository(session).listar(limite=50)
    finally:
        get_settings.cache_clear()

    acciones = [r["accion"] for r in registros]
    assert "DISPOSITIVO_RECHAZADO" in acciones


@pytest.mark.asyncio
async def test_lectura_con_timestamp_futuro_se_rechaza_y_se_audita(entorno):
    """Un reloj adelantado del nodo (o un intento de antedatar evidencia) no
    debe entrar en la cadena. El motivo tiene que quedar registrado, o el
    dispositivo dejaría de reportar sin explicación visible."""
    from datetime import timedelta

    from src.infrastructure.database.repositories.audit_log_repository import (
        SQLAlchemyAuditLogRepository,
    )

    broadcaster = _BroadcasterEspia()
    futuro = datetime.now(tz=timezone.utc) + timedelta(days=2)

    await _consumir(
        [_MensajeFalso(TOPIC, _payload_firmware(timestamp=futuro.strftime("%Y-%m-%dT%H:%M:%SZ")))],
        broadcaster,
    )

    assert await _lecturas(entorno) == []

    async with entorno() as session:
        registros = await SQLAlchemyAuditLogRepository(session).listar(limite=50)

    assert any("RECHAZADA" in r["accion"] for r in registros), [r["accion"] for r in registros]
