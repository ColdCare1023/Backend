"""BE-002: aprovisionamiento y revocación de credenciales de dispositivos en el broker.

La autenticación de los nodos es la integrada de EMQX (usuario = device_id,
contraseña = token), como describen OE2.I1, OE1.I2 y el criterio de HU-10. El
backend genera el token, lo entrega una sola vez y lo aprovisiona en la base
de autenticación del broker mediante la API REST de administración de EMQX:

- emitir o rotar: crea el usuario o reemplaza su contraseña;
- revocar o dar de baja: elimina el usuario;
- además expulsa la sesión MQTT vigente (`DELETE /clients/{clientid}`), porque
  una sesión ya abierta seguiría publicando hasta cerrarse.

Con `BROKER_USUARIOS_MODO=manual` (EMQX Cloud Serverless, cuya API no tiene
endpoints de usuarios) las dos primeras operaciones no se llaman: el usuario se
crea o retira a mano en la consola de la instancia y el backend lo informa como
pendiente. La expulsión de sesiones sí está en la API de Serverless.

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
# Código de error de EMQX 5 para `DELETE /clients/{clientid}` sin sesión abierta.
_CLIENTE_INEXISTENTE = "CLIENTID_NOT_FOUND"


def _codigo_error(respuesta: httpx.Response) -> str | None:
    """Campo `code` del cuerpo de error de EMQX; None si el cuerpo no es ese JSON."""
    try:
        cuerpo = respuesta.json()
    except ValueError:
        return None
    return cuerpo.get("code") if isinstance(cuerpo, dict) else None


class BrokerAdmin:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._url = settings.broker_api_url.rstrip("/")
        self._key = settings.broker_api_key
        self._secret = settings.broker_api_secret
        self._gestiona_usuarios = settings.broker_usuarios_modo == "api"
        self._transport = transport

    @property
    def configurado(self) -> bool:
        return bool(self._url and self._key and self._secret)

    @property
    def gestiona_usuarios(self) -> bool:
        """True si el backend puede crear y eliminar usuarios por la API del broker."""
        return self._gestiona_usuarios and self.configurado

    def _cliente(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=10, transport=self._transport, auth=(self._key, self._secret))

    async def aprovisionar_credencial(self, device_id: str, token: str) -> bool | None:
        """Deja `token` como contraseña de `device_id` en el broker.

        True: aprovisionada (usuario creado o contraseña reemplazada). False: el
        broker no la aceptó (error o inaccesible). None: no se intentó (modo manual
        o sin API del broker): el usuario debe crearse a mano en la consola.
        """
        if not self.gestiona_usuarios:
            logger.warning(
                "El broker no gestiona usuarios por API (modo manual o API sin configurar): "
                "la credencial de %s queda pendiente de aprovisionar en la consola.",
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
        None: no se intentó (modo manual o sin API del broker): el usuario debe
        retirarse a mano en la consola.
        """
        if not self.gestiona_usuarios:
            logger.warning(
                "El broker no gestiona usuarios por API (modo manual o API sin configurar): "
                "la credencial de %s queda pendiente de retirar en la consola.",
                device_id,
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

        True: cerrada, o no había sesión abierta (404 con CLIENTID_NOT_FOUND).
        False: el broker no la cerró (error, inaccesible o ruta equivocada).
        None: no hay API del broker configurada.
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
        if respuesta.status_code == 204:
            return True
        if respuesta.status_code == 404:
            # Solo el código de EMQX distingue «no había sesión» de una ruta
            # equivocada: con BROKER_API_URL sin /api/v5 cada expulsión
            # respondía 404 y se daba por hecha sin haberla hecho.
            if _codigo_error(respuesta) == _CLIENTE_INEXISTENTE:
                return True
            logger.error(
                "El broker respondió 404 sin %s al expulsar a %s: ruta de la API del broker incorrecta "
                "(revise BROKER_API_URL).",
                _CLIENTE_INEXISTENTE,
                client_id,
            )
            return False
        logger.error("El broker no expulsó a %s (HTTP %s).", client_id, respuesta.status_code)
        return False


def obtener_broker_admin(settings: Settings) -> BrokerAdmin:
    """Dependencia sustituible en pruebas (no se sale a la red)."""
    return BrokerAdmin(settings)
