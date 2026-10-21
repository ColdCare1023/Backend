"""BE-025: correos transaccionales (recuperación de contraseña) por el mismo
SMTP que las alertas. Nunca lanza: un SMTP caído se registra y se informa
con False."""

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from src.infrastructure.config import Settings

logger = logging.getLogger("infrastructure.notifications.correo_transaccional")


class EnviadorCorreo:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def configurado(self) -> bool:
        s = self._settings
        return bool(s.smtp_enabled and s.smtp_host and s.smtp_user and s.smtp_password and s.smtp_from)

    async def enviar(self, destino: str, asunto: str, cuerpo: str) -> bool:
        if not self.configurado:
            logger.warning("SMTP no configurado: no se envió el correo transaccional.")
            return False
        mensaje = EmailMessage()
        mensaje["Subject"] = asunto
        mensaje["From"] = self._settings.smtp_from
        mensaje["To"] = destino
        mensaje.set_content(cuerpo)
        try:
            await asyncio.to_thread(self._smtp_enviar, mensaje)
        except Exception:
            # Sin el destinatario en el log: es un dato personal.
            logger.exception("No se pudo enviar un correo transaccional.")
            return False
        return True

    def _smtp_enviar(self, mensaje: EmailMessage) -> None:
        with smtplib.SMTP_SSL(self._settings.smtp_host, self._settings.smtp_port, timeout=10) as servidor:
            servidor.login(self._settings.smtp_user, self._settings.smtp_password)
            servidor.send_message(mensaje)
