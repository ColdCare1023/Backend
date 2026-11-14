"""BE-025: correos transaccionales (recuperación de contraseña, códigos de
verificación) por el mismo transporte que las alertas: SMTP (respetando
SMTP_SEGURIDAD), API HTTP o el relé de Apps Script, según EMAIL_TRANSPORTE.

Nunca lanza: un proveedor caído se registra y se informa con False."""

import logging

from src.infrastructure.config import Settings
from src.infrastructure.notifications.transporte_email import (
    MensajeCorreo,
    TransporteEmail,
    construir_transporte,
)

logger = logging.getLogger("infrastructure.notifications.correo_transaccional")


class EnviadorCorreo:
    def __init__(self, settings: Settings, transporte: TransporteEmail | None = None) -> None:
        self._settings = settings
        self._transporte = transporte if transporte is not None else construir_transporte(settings)

    @property
    def configurado(self) -> bool:
        return self._transporte is not None and bool(self._settings.correo_remitente)

    async def enviar(self, destino: str, asunto: str, cuerpo: str, html: str | None = None) -> bool:
        if not self.configurado or self._transporte is None:
            logger.warning("Correo no configurado: no se envió el correo transaccional.")
            return False
        mensaje = MensajeCorreo(
            remitente=self._settings.correo_remitente, destinatario=destino, asunto=asunto, cuerpo=cuerpo, html=html
        )
        try:
            await self._transporte.enviar(mensaje)
        except Exception as exc:
            # Sin el destinatario ni el cuerpo en el log: llevan datos personales
            # y, en los códigos de verificación, el propio secreto. El texto de un
            # error SMTP puede citar al destinatario, así que de él solo va el tipo.
            nombre = self._transporte.nombre
            detalle = type(exc.__cause__ or exc).__name__ if nombre == "smtp" else str(exc)
            logger.error("No se pudo enviar un correo transaccional (%s): %s", nombre, detalle)
            return False
        return True
