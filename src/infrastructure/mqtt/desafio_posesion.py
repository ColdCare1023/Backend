"""HU-44: publicación del desafío de posesión desde la API (confirmar consola).

Cliente MQTT efímero con identificador propio, como las órdenes OTA: reutilizar
el del consumidor de ingesta haría que el broker expulsara esa sesión. Se
publica en `farmacias/<device_id>/ack`, donde el backend ya puede publicar y el
nodo ya escucha (sin reglas ACL nuevas). Si el nodo no lo recibe (estaba
reconectando), la ingesta lo vuelve a emitir con el siguiente mensaje retenido.
"""

import logging

import aiomqtt

from src.infrastructure.config import Settings
from src.infrastructure.mqtt.mqtt_client import build_ssl_context

logger = logging.getLogger("infrastructure.mqtt.desafio_posesion")


def topico_desafio(device_id: str) -> str:
    return f"farmacias/{device_id}/ack"


class PublicadorDesafios:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def configurado(self) -> bool:
        return bool(self._settings.mqtt_enabled and self._settings.mqtt_host)

    async def publicar(self, device_id: str, mensaje: str) -> bool:
        """True si el broker confirmó la publicación (QoS 1)."""
        if not self.configurado:
            return False
        s = self._settings
        try:
            async with aiomqtt.Client(
                hostname=s.mqtt_host,
                port=s.mqtt_port,
                username=s.mqtt_username,
                password=s.mqtt_password,
                identifier=f"{s.mqtt_client_id}-pop",
                tls_context=build_ssl_context(s.mqtt_tls_enabled, s.mqtt_ca_file),
                keepalive=30,
            ) as cliente:
                await cliente.publish(topico_desafio(device_id), mensaje, qos=1)
        except aiomqtt.MqttError:
            logger.exception("No se pudo publicar el desafío de posesión para %s.", device_id)
            return False
        return True


def obtener_publicador_desafios(settings: Settings) -> PublicadorDesafios:
    """Dependencia sustituible en pruebas (no se sale a la red)."""
    return PublicadorDesafios(settings)
