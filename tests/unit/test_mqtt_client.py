"""Pruebas del cliente MQTT (RF-05/RF-06, RNF-05).

Este módulo no tenía ninguna prueba: por eso convivió durante meses con una
llamada al constructor de aiomqtt escrita contra la API 1.x, que en la 2.x
lanza TypeError antes de intentar conectarse siquiera.
"""

import asyncio
import ssl

import aiomqtt
import pytest

from src.infrastructure.config import Settings
from src.infrastructure.mqtt import mqtt_client


def _settings(**extra) -> Settings:
    base = {
        "environment": "test",
        "mqtt_host": "broker.ejemplo.test",
        "mqtt_port": 8883,
        "mqtt_username": "backend_service",
        "mqtt_password": "clave-de-broker",
        "mqtt_client_id": "backend-pruebas",
    }
    base.update(extra)
    return Settings(**base)


@pytest.mark.asyncio
async def test_build_client_construye_un_cliente_aiomqtt_real():
    """S-01: con los nombres de parámetro de aiomqtt 1.x esto lanzaba
    `TypeError: unexpected keyword argument 'ssl_context'`, de modo que la
    ingesta MQTT no funcionaba en ningún despliegue."""
    cliente = mqtt_client.build_client(_settings(mqtt_tls_enabled=True))

    assert isinstance(cliente, aiomqtt.Client)


@pytest.mark.asyncio
async def test_build_client_con_tls_habilitado_pasa_un_contexto_ssl():
    """RNF-05: el transporte ESP32↔broker↔backend debe ir cifrado."""
    cliente = mqtt_client.build_client(_settings(mqtt_tls_enabled=True))

    assert isinstance(cliente._client._ssl_context, ssl.SSLContext)


@pytest.mark.asyncio
async def test_build_client_sin_tls_no_configura_contexto_ssl():
    cliente = mqtt_client.build_client(_settings(mqtt_tls_enabled=False))

    assert cliente._client._ssl_context is None


def test_build_ssl_context_verifica_certificado_y_hostname():
    contexto = mqtt_client.build_ssl_context(True)

    assert contexto.verify_mode == ssl.CERT_REQUIRED
    assert contexto.check_hostname is True
    assert mqtt_client.build_ssl_context(False) is None


class _MensajeFalso:
    topic = "farmacias/ESP32-01/lecturas"
    payload = b"{}"


class _ClienteFalso:
    """Cliente mínimo con `messages` como PROPIEDAD, igual que aiomqtt 2.x."""

    def __init__(self, mensajes):
        self._mensajes = mensajes

    @property
    def messages(self):
        async def generador():
            for mensaje in self._mensajes:
                yield mensaje

        return generador()


@pytest.mark.asyncio
async def test_consumir_mensajes_entrega_cada_mensaje_al_manejador():
    """`client.messages` es propiedad, no método: invocarla como `messages()`
    reventaba el consumidor en el primer mensaje."""
    recibidos = []

    async def manejador(cliente, mensaje):
        recibidos.append(mensaje)

    await mqtt_client.consumir_mensajes(_ClienteFalso([_MensajeFalso(), _MensajeFalso()]), manejador)

    assert len(recibidos) == 2


@pytest.mark.asyncio
async def test_un_mensaje_que_falla_no_detiene_el_consumo_de_los_siguientes():
    procesados = []

    async def manejador(cliente, mensaje):
        procesados.append(mensaje)
        if len(procesados) == 1:
            raise ValueError("payload corrupto")

    await mqtt_client.consumir_mensajes(_ClienteFalso([_MensajeFalso(), _MensajeFalso()]), manejador)

    assert len(procesados) == 2


@pytest.mark.asyncio
async def test_la_ingesta_se_reconecta_tras_una_caida_del_broker(monkeypatch):
    """S-02: antes, la primera MqttError terminaba la tarea consumidora en
    silencio y el backend dejaba de ingerir lecturas indefinidamente."""
    intentos = []

    async def sesion_que_cae(settings, manejador):
        intentos.append(settings)
        raise aiomqtt.MqttError("broker inaccesible")

    monkeypatch.setattr(mqtt_client, "_sesion_una_vez", sesion_que_cae)
    monkeypatch.setattr(mqtt_client, "RECONEXION_ESPERA_INICIAL_SEGUNDOS", 0.001)
    monkeypatch.setattr(mqtt_client, "RECONEXION_ESPERA_MAXIMA_SEGUNDOS", 0.001)

    async def manejador(mensaje):  # pragma: no cover - nunca llega a llamarse
        return None

    tarea = asyncio.create_task(mqtt_client.consumir_con_reconexion(_settings(), manejador))
    await asyncio.sleep(0.05)
    tarea.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarea

    assert len(intentos) > 1, "la ingesta debe reintentar la conexión, no rendirse"


@pytest.mark.asyncio
async def test_la_reconexion_tambien_sobrevive_a_errores_no_mqtt(monkeypatch):
    """Un fallo de configuración TLS o de DNS no es un MqttError; tampoco debe
    matar la ingesta de forma permanente."""
    intentos = []

    async def sesion_que_cae(settings, manejador):
        intentos.append(settings)
        raise OSError("no se pudo resolver el host")

    monkeypatch.setattr(mqtt_client, "_sesion_una_vez", sesion_que_cae)
    monkeypatch.setattr(mqtt_client, "RECONEXION_ESPERA_INICIAL_SEGUNDOS", 0.001)
    monkeypatch.setattr(mqtt_client, "RECONEXION_ESPERA_MAXIMA_SEGUNDOS", 0.001)

    async def manejador(mensaje):  # pragma: no cover
        return None

    tarea = asyncio.create_task(mqtt_client.consumir_con_reconexion(_settings(), manejador))
    await asyncio.sleep(0.05)
    tarea.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarea

    assert len(intentos) > 1


@pytest.mark.asyncio
async def test_tras_una_sesion_que_conecto_la_espera_vuelve_a_1_s(monkeypatch):
    """HU-12 esc. 3 / HU-55: tras una sesión que llegó a conectar, la espera de
    reconexión vuelve a 1 s. Antes seguía duplicándose desde el último fallo:
    tras varios cortes, una caída breve del broker dejaba la ingesta parada
    hasta 60 s aunque el broker ya estuviera disponible."""
    # 3 fallos sin conectar, 1 sesión que se sostiene 45 s y cae, 2 fallos más.
    guion = [False, False, False, True, False, False]
    esperas: list[float] = []
    reloj = {"t": 1000.0}
    monkeypatch.setattr(mqtt_client, "_monotonic", lambda: reloj["t"])

    async def sesion(settings, manejador):
        conecta = guion.pop(0)
        if conecta:
            mqtt_client.ESTADO_CONEXION.marcar(True)
            reloj["t"] += 45
            mqtt_client.ESTADO_CONEXION.marcar(False)
        raise aiomqtt.MqttError("broker inaccesible")

    async def dormir(segundos):
        esperas.append(segundos)
        if not guion:
            raise asyncio.CancelledError

    monkeypatch.setattr(mqtt_client, "_sesion_una_vez", sesion)
    monkeypatch.setattr(mqtt_client.asyncio, "sleep", dormir)

    async def manejador(mensaje):  # pragma: no cover
        return None

    with pytest.raises(asyncio.CancelledError):
        await mqtt_client.consumir_con_reconexion(_settings(), manejador)

    assert esperas == [1.0, 2.0, 4.0, 1.0, 2.0, 4.0]


@pytest.mark.asyncio
async def test_sesiones_que_caen_enseguida_no_reinician_la_espera(monkeypatch):
    """HU-12 (O-1): dos instancias con el mismo client_id (solape de un
    redeploy) se expulsan mutuamente; cada sesión dura un instante. La espera
    sigue el backoff en vez de reintentar cada segundo."""
    guion = [True, True, True, True]
    esperas: list[float] = []
    reloj = {"t": 1000.0}
    monkeypatch.setattr(mqtt_client, "_monotonic", lambda: reloj["t"])

    async def sesion(settings, manejador):
        guion.pop(0)
        mqtt_client.ESTADO_CONEXION.marcar(True)
        reloj["t"] += 2  # el broker la expulsa a los 2 s
        mqtt_client.ESTADO_CONEXION.marcar(False)
        raise aiomqtt.MqttError("sesión tomada por otro cliente")

    async def dormir(segundos):
        esperas.append(segundos)
        if not guion:
            raise asyncio.CancelledError

    monkeypatch.setattr(mqtt_client, "_sesion_una_vez", sesion)
    monkeypatch.setattr(mqtt_client.asyncio, "sleep", dormir)

    async def manejador(mensaje):  # pragma: no cover
        return None

    with pytest.raises(asyncio.CancelledError):
        await mqtt_client.consumir_con_reconexion(_settings(), manejador)

    assert esperas == [1.0, 2.0, 4.0, 8.0]
    assert mqtt_client.ESTADO_CONEXION.ultima_sesion_s == 2


@pytest.mark.asyncio
async def test_sin_conectar_la_espera_crece_hasta_el_tope(monkeypatch):
    """La espera sigue creciendo (1, 2, 4… 60 s) mientras no se conecte nunca."""
    intentos = {"n": 0}
    esperas: list[float] = []

    async def sesion(settings, manejador):
        intentos["n"] += 1
        raise aiomqtt.MqttError("broker inaccesible")

    async def dormir(segundos):
        esperas.append(segundos)
        if len(esperas) >= 9:
            raise asyncio.CancelledError

    monkeypatch.setattr(mqtt_client, "_sesion_una_vez", sesion)
    monkeypatch.setattr(mqtt_client.asyncio, "sleep", dormir)

    async def manejador(mensaje):  # pragma: no cover
        return None

    with pytest.raises(asyncio.CancelledError):
        await mqtt_client.consumir_con_reconexion(_settings(), manejador)

    assert esperas == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0]


@pytest.mark.asyncio
async def test_mqtt_session_cancela_la_tarea_al_salir(monkeypatch):
    """Al apagarse el backend no debe quedar una tarea consumidora huérfana
    reintentando conexiones contra el broker."""

    async def sesion_que_cae(settings, manejador):
        await asyncio.sleep(3600)

    monkeypatch.setattr(mqtt_client, "_sesion_una_vez", sesion_que_cae)

    async def manejador(mensaje):  # pragma: no cover
        return None

    async with mqtt_client.mqtt_session(_settings(), manejador) as tarea:
        assert not tarea.done()

    assert tarea.cancelled() or tarea.done()


class _ClienteSuscripcion:
    def __init__(self, respuesta) -> None:
        self._respuesta = respuesta

    async def subscribe(self, topic, qos=0):
        return self._respuesta


@pytest.mark.asyncio
async def test_una_suscripcion_rechazada_por_el_broker_fuerza_la_reconexion():
    """SUBACK 0x80: sin esta comprobación el backend quedaba conectado y sordo."""
    with pytest.raises(aiomqtt.MqttError, match="rechazó la suscripción"):
        await mqtt_client.suscribir_o_fallar(_ClienteSuscripcion((128,)), mqtt_client.TOPIC_LECTURAS)


@pytest.mark.asyncio
async def test_una_suscripcion_concedida_en_qos_1_no_falla():
    await mqtt_client.suscribir_o_fallar(_ClienteSuscripcion((1,)), mqtt_client.TOPIC_LECTURAS)


@pytest.mark.asyncio
async def test_interpreta_los_reasoncode_que_devuelve_aiomqtt():
    """aiomqtt 2.x no devuelve enteros sino ReasonCode de paho."""
    from paho.mqtt.packettypes import PacketTypes
    from paho.mqtt.reasoncodes import ReasonCode

    concedido = [ReasonCode(PacketTypes.SUBACK, identifier=1)]
    await mqtt_client.suscribir_o_fallar(_ClienteSuscripcion(concedido), mqtt_client.TOPIC_LECTURAS)
    rechazado = [ReasonCode(PacketTypes.SUBACK, identifier=0x80)]
    with pytest.raises(aiomqtt.MqttError):
        await mqtt_client.suscribir_o_fallar(_ClienteSuscripcion(rechazado), mqtt_client.TOPIC_LECTURAS)
