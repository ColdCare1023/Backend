"""IOT-004: publicación de órdenes OTA en `farmacias/<device_id>/ota`.

Cliente MQTT efímero con identificador propio: reutilizar el del consumidor
de ingesta haría que el broker expulsara esa sesión (un client id = una
sesión). Solo el usuario de servicio del backend puede publicar en el tópico
(ACL); el nodo solo lo escucha.
"""

import json
import logging

import aiomqtt

from src.infrastructure.config import Settings
from src.infrastructure.mqtt.mqtt_client import build_ssl_context

logger = logging.getLogger("infrastructure.mqtt.ordenes_ota")


def topico_ota(device_id: str) -> str:
    return f"farmacias/{device_id}/ota"


class PublicadorOrdenesOTA:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def configurado(self) -> bool:
        return bool(self._settings.mqtt_enabled and self._settings.mqtt_host)

    async def publicar(self, device_id: str, orden: dict) -> bool:
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
                identifier=f"{s.mqtt_client_id}-ota",
                tls_context=build_ssl_context(s.mqtt_tls_enabled, s.mqtt_ca_file),
                keepalive=30,
            ) as cliente:
                await cliente.publish(topico_ota(device_id), json.dumps(orden, separators=(",", ":")), qos=1)
        except aiomqtt.MqttError:
            logger.exception("No se pudo publicar la orden OTA para %s.", device_id)
            return False
        return True


def obtener_publicador_ota(settings: Settings) -> PublicadorOrdenesOTA:
    """Dependencia sustituible en pruebas (no se sale a la red)."""
    return PublicadorOrdenesOTA(settings)
