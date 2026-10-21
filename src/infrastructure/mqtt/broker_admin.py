"""BE-002: punto de aplicación de la revocación de credenciales en el broker.

Revocar o rotar la credencial de un dispositivo en la base de datos no basta:
una sesión MQTT ya abierta sigue publicando hasta que se cierre. Este cliente
usa la API REST de administración de EMQX (`DELETE /clients/{clientid}`) para
expulsar la sesión vigente; al reconectar, el broker vuelve a consultar el
webhook `/api/dispositivos/autenticar`, que ya responde con el estado nuevo.

La API de EMQX se autentica con HTTP Basic usando una API key (no un usuario
del dashboard). Sin URL o sin API key configuradas el cliente no hace nada y
lo informa: el llamador decide cómo registrarlo.
"""

import logging
from urllib.parse import quote

import httpx

from src.infrastructure.config import Settings

logger = logging.getLogger("infrastructure.mqtt.broker_admin")


class BrokerAdmin:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._url = settings.broker_api_url.rstrip("/")
        self._key = settings.broker_api_key
        self._secret = settings.broker_api_secret
        self._transport = transport

    @property
    def configurado(self) -> bool:
        return bool(self._url and self._key and self._secret)

    async def desconectar_cliente(self, client_id: str) -> bool | None:
        """Expulsa la sesión MQTT de `client_id`.

        True: cerrada, o no había sesión abierta (404). False: el broker no la
        cerró (error o inaccesible). None: no hay API del broker configurada.
        """
        if not self.configurado:
            logger.warning(
                "API de administración del broker no configurada: la sesión de %s no se expulsó.", client_id
            )
            return None
        try:
            async with httpx.AsyncClient(timeout=10, transport=self._transport) as cliente:
                respuesta = await cliente.delete(
                    f"{self._url}/clients/{quote(client_id, safe='')}", auth=(self._key, self._secret)
                )
        except httpx.HTTPError:
            logger.exception("No se pudo contactar la API del broker para expulsar a %s.", client_id)
            return False
        if respuesta.status_code in (204, 404):
            return True
        logger.error("El broker no expulsó a %s (HTTP %s).", client_id, respuesta.status_code)
        return False


def obtener_broker_admin(settings: Settings) -> BrokerAdmin:
    """Dependencia sustituible en pruebas (no se sale a la red)."""
    return BrokerAdmin(settings)
