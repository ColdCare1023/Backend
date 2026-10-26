"""BE-002: aprovisionamiento y revocación de credenciales de dispositivos en el broker.

La autenticación de los nodos es la integrada de EMQX (usuario = device_id,
contraseña = token), como describen OE2.I1, OE1.I2 y el criterio de HU-10. El
backend genera el token, lo entrega una sola vez y lo aprovisiona en la base
de autenticación del broker mediante la API REST de administración de EMQX:

- emitir o rotar: crea el usuario o reemplaza su contraseña;
- revocar o dar de baja: elimina el usuario;
- además expulsa la sesión MQTT vigente (`DELETE /clients/{clientid}`), porque
  una sesión ya abierta seguiría publicando hasta cerrarse.

La API de EMQX se autentica con HTTP Basic usando una API key (no un usuario
del dashboard). Sin URL o sin API key configuradas el cliente no hace nada y
lo informa: el llamador decide cómo registrarlo.
"""

import logging
from urllib.parse import quote

import httpx

from src.infrastructure.config import Settings

logger = logging.getLogger("infrastructure.mqtt.broker_admin")

# Autenticador integrado de EMQX (mecanismo password_based, backend built_in_database).
_USUARIOS = "/authentication/password_based:built_in_database/users"


class BrokerAdmin:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._url = settings.broker_api_url.rstrip("/")
        self._key = settings.broker_api_key
        self._secret = settings.broker_api_secret
        self._transport = transport

    @property
    def configurado(self) -> bool:
        return bool(self._url and self._key and self._secret)

    def _cliente(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=10, transport=self._transport, auth=(self._key, self._secret))

    async def aprovisionar_credencial(self, device_id: str, token: str) -> bool | None:
        """Deja `token` como contraseña de `device_id` en el broker.

        True: aprovisionada (usuario creado o contraseña reemplazada). False: el
        broker no la aceptó (error o inaccesible). None: no hay API del broker
        configurada.
        """
        if not self.configurado:
            logger.warning(
                "API de administración del broker no configurada: la credencial de %s no se aprovisionó.",
                device_id,
            )
            return None
        try:
            async with self._cliente() as cliente:
                respuesta = await cliente.post(
                    f"{self._url}{_USUARIOS}", json={"user_id": device_id, "password": token}
                )
                if respuesta.status_code == 409:  # el usuario ya existe: se reemplaza su contraseña
                    respuesta = await cliente.put(
                        f"{self._url}{_USUARIOS}/{quote(device_id, safe='')}", json={"password": token}
                    )
        except httpx.HTTPError:
            logger.exception("No se pudo contactar la API del broker para aprovisionar a %s.", device_id)
            return False
        if respuesta.status_code in (200, 201, 204):
            return True
        logger.error("El broker no aprovisionó la credencial de %s (HTTP %s).", device_id, respuesta.status_code)
        return False

    async def eliminar_credencial(self, device_id: str) -> bool | None:
        """Elimina el usuario `device_id` del broker (revocación o baja).

        True: eliminado, o no existía (404). False: el broker no lo eliminó.
        None: no hay API del broker configurada.
        """
        if not self.configurado:
            logger.warning(
                "API de administración del broker no configurada: la credencial de %s no se eliminó.", device_id
            )
            return None
        try:
            async with self._cliente() as cliente:
                respuesta = await cliente.delete(f"{self._url}{_USUARIOS}/{quote(device_id, safe='')}")
        except httpx.HTTPError:
            logger.exception("No se pudo contactar la API del broker para eliminar a %s.", device_id)
            return False
        if respuesta.status_code in (204, 404):
            return True
        logger.error("El broker no eliminó la credencial de %s (HTTP %s).", device_id, respuesta.status_code)
        return False

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
            async with self._cliente() as cliente:
                respuesta = await cliente.delete(f"{self._url}/clients/{quote(client_id, safe='')}")
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
