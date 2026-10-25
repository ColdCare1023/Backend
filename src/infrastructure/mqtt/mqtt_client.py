import asyncio
import contextlib
import logging
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import aiomqtt

from src.infrastructure.config import Settings

logger = logging.getLogger("infrastructure.mqtt.mqtt_client")

TOPIC_LECTURAS = "farmacias/+/lecturas"
TOPIC_EVENTOS = "farmacias/+/eventos"

# Reconexión con espera creciente: un broker gestionado (EMQX Cloud) puede
# quedar inaccesible unos segundos por mantenimiento o corte de red. Reintentar
# en bucle cerrado consumiría CPU y cuota de conexiones del plan; esperar
# minutos retrasaría demasiado la reanudación de la ingesta (RF-05).
RECONEXION_ESPERA_INICIAL_SEGUNDOS = 1.0
RECONEXION_ESPERA_MAXIMA_SEGUNDOS = 60.0

# HU-07: el manejador recibe también el cliente activo, no solo el mensaje.
# El buffer offline del ESP32 (LittleFS) solo debe borrar un bloque cuando el
# backend confirma que la lectura hizo COMMIT en PostgreSQL — un PUBACK de
# MQTT QoS1 por sí solo únicamente confirma que el broker recibió el
# paquete, no que la base de datos lo persistió. Sin acceso al cliente aquí,
# el manejador no tendría forma de publicar ese acuse lógico de aplicación.
MensajeHandler = Callable[[aiomqtt.Client, aiomqtt.Message], Awaitable[None]]


@dataclass
class EstadoConexionMQTT:
    """HU-56: estado observable de la ingesta para `/health/detalle`. La tarea
    consumidora puede estar viva y aun así desconectada (reintentando), que es
    justo lo que un operador necesita distinguir."""

    conectado: bool = False
    desde: datetime | None = None

    def marcar(self, conectado: bool) -> None:
        if conectado != self.conectado or self.desde is None:
            self.conectado = conectado
            self.desde = datetime.now(tz=timezone.utc)


# Un solo proceso por diseño (README, «Topología»): un estado de módulo basta.
ESTADO_CONEXION = EstadoConexionMQTT()


def build_ssl_context(tls_enabled: bool, ca_file: str = "") -> ssl.SSLContext | None:
    """Contexto TLS 1.2/1.3 con verificación del certificado del broker (RNF-05).

    `create_default_context()` exige certificado válido y comprobación del
    nombre de host. Devolver None deshabilita TLS por completo; solo tiene
    sentido contra un broker local de desarrollo. `ca_file` añade una CA
    privada (banco local) sin relajar la verificación.
    """
    if not tls_enabled:
        return None
    return ssl.create_default_context(cafile=ca_file or None)


def build_client(settings: Settings) -> aiomqtt.Client:
    """Cliente aiomqtt 2.x.

    Los nombres de los parámetros son los de aiomqtt 2 (`tls_context`,
    `keepalive`, `password` como str). Con los nombres de la 1.x el
    constructor lanza TypeError y la ingesta MQTT no llega ni a intentar
    conectarse — ver hallazgo S-01.
    """
    return aiomqtt.Client(
        hostname=settings.mqtt_host,
        port=settings.mqtt_port,
        username=settings.mqtt_username,
        password=settings.mqtt_password,
        identifier=settings.mqtt_client_id,
        tls_context=build_ssl_context(settings.mqtt_tls_enabled, settings.mqtt_ca_file),
        keepalive=60,
        # HU-13 criterio 5 / HU-12: sesión PERSISTENTE. Con sesión limpia (el
        # valor por defecto) el broker descarta la suscripción al caer el
        # backend, y un LWT —o lecturas QoS 1— publicados mientras tanto se
        # pierden: el nodo apagado seguía figurando «online» (probado con el
        # nodo real el 8-oct-2026). Exige `client_id` fijo y una sola instancia
        # (README, «Topología»).
        clean_session=False,
    )


async def consumir_mensajes(client: aiomqtt.Client, manejador: MensajeHandler) -> None:
    # En aiomqtt 2.x `messages` es una propiedad, no un método.
    async for message in client.messages:
        try:
            await manejador(client, message)
        except Exception:
            # No se propaga (un mensaje malformado o un error inesperado del
            # pipeline —incluida la inferencia de IA— no debe tumbar el
            # consumidor MQTT), pero SÍ se audita en logs (corrige el hallazgo
            # AI-02: antes este descarte era completamente silencioso, sin
            # distinguir un fallo de inferencia de un bug de programación).
            logger.exception(
                "Error no controlado procesando mensaje MQTT en topic %s; descartado.",
                message.topic,
            )
            continue


async def suscribir_o_fallar(client: aiomqtt.Client, topic: str) -> None:
    """Suscribe en QoS 1 y exige que el broker lo conceda.

    Un SUBACK con código de rechazo (>= 0x80) no lanza excepción en aiomqtt:
    el backend quedaba conectado pero sin suscripciones, sin recibir lecturas y
    sin ningún error visible. Pasó al reiniciar EMQX: el backend se reconectó
    antes de que el broker cargara sus reglas de autorización y la
    suscripción se denegó. Al fallar aquí, la reconexión con backoff vuelve a
    intentarlo cuando el broker ya está listo.
    """
    concedidos = await client.subscribe(topic, qos=1)
    # aiomqtt 2.x entrega `ReasonCode` de paho (también en MQTT 3.1.1); el
    # código numérico está en `.value`.
    if any(getattr(codigo, "value", codigo) >= 0x80 for codigo in concedidos):
        raise aiomqtt.MqttError(f"El broker rechazó la suscripción a {topic} (SUBACK {list(concedidos)})")


async def _sesion_una_vez(settings: Settings, manejador: MensajeHandler) -> None:
    """Una conexión completa: conectar, suscribirse y consumir hasta que caiga."""
    async with build_client(settings) as client:
        # QoS 1 (HU-11): el broker entrega a cada suscriptor con el menor QoS
        # entre publicación y suscripción; suscrito en QoS 0, el tramo
        # broker→backend era "como mucho una vez" aunque el nodo publique en 1.
        for topic in (TOPIC_LECTURAS, TOPIC_EVENTOS):
            await suscribir_o_fallar(client, topic)
        logger.info("Conectado al broker MQTT %s:%s", settings.mqtt_host, settings.mqtt_port)
        ESTADO_CONEXION.marcar(True)
        try:
            await consumir_mensajes(client, manejador)
        finally:
            ESTADO_CONEXION.marcar(False)


async def consumir_con_reconexion(settings: Settings, manejador: MensajeHandler) -> None:
    """Mantiene viva la ingesta MQTT reconectando ante caídas del broker.

    Sin este bucle la primera desconexión terminaba la tarea consumidora en
    silencio: el backend seguía respondiendo por HTTP mientras dejaba de
    recibir lecturas de los ESP32 indefinidamente (RF-05/RF-06). Es el peor
    modo de fallo posible aquí, porque no es visible desde fuera.
    """
    espera = RECONEXION_ESPERA_INICIAL_SEGUNDOS
    while True:
        try:
            await _sesion_una_vez(settings, manejador)
            # El iterador terminó sin error: el broker cerró la sesión.
            logger.warning("El flujo de mensajes MQTT terminó; se reintentará la conexión.")
        except asyncio.CancelledError:
            raise
        except aiomqtt.MqttError as exc:
            logger.warning("Conexión MQTT perdida (%s). Reintentando en %.0f s.", exc, espera)
        except Exception:
            logger.exception("Error inesperado en la sesión MQTT. Reintentando en %.0f s.", espera)

        await asyncio.sleep(espera)
        espera = min(espera * 2, RECONEXION_ESPERA_MAXIMA_SEGUNDOS)


@contextlib.asynccontextmanager
async def mqtt_session(settings: Settings, manejador: MensajeHandler) -> AsyncIterator[asyncio.Task]:
    """Ciclo de vida de la ingesta MQTT para el lifespan de FastAPI.

    Cede la tarea consumidora y no un cliente: con reconexión automática el
    cliente concreto cambia en cada reintento, así que guardar la referencia a
    uno solo daría una idea falsa de sesión estable.
    """
    task = asyncio.create_task(consumir_con_reconexion(settings, manejador))
    try:
        yield task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
