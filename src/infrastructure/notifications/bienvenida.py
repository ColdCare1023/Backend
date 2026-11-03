"""Aviso real al crear una cuenta: correo y SMS al usuario recién registrado.

Usa los mismos transportes de producción que las alertas (SMTP/Resend para el
correo, pasarela compatible con Twilio para el SMS); no hay un canal de prueba
aparte. Nunca incluye la contraseña: la entrega el administrador. Nunca lanza:
cada canal devuelve su resultado y quien llama lo audita.
"""

import logging
from dataclasses import dataclass

from src.infrastructure.config import Settings
from src.infrastructure.notifications.correos_cuenta import correo_cuenta_creada
from src.infrastructure.notifications.notificacion_service import obtener_notificacion_service
from src.infrastructure.notifications.transporte_email import (
    ErrorEnvioCorreo,
    MensajeCorreo,
    TransporteEmail,
    construir_transporte,
)

logger = logging.getLogger("infrastructure.notifications.bienvenida")

ENVIADO = "enviado"
FALLIDO = "fallido"
NO_CONFIGURADO = "no_configurado"
SIN_TELEFONO = "sin_telefono"


@dataclass(frozen=True, slots=True)
class ResultadoBienvenida:
    correo: str
    sms: str


def componer_correo(settings: Settings, nombre: str, email: str) -> MensajeCorreo:
    correo = correo_cuenta_creada(nombre, email, settings.frontend_url)
    return MensajeCorreo(
        remitente=settings.correo_remitente,
        destinatario=email,
        asunto=correo.asunto,
        cuerpo=correo.cuerpo,
        html=correo.html,
    )


def componer_sms(settings: Settings) -> str:
    enlace = f" {settings.frontend_url.rstrip('/')}" if settings.frontend_url else ""
    return f"ColdCare: se creo tu cuenta. Tu usuario es tu correo; el administrador te dara la clave temporal.{enlace}"


class NotificadorBienvenida:
    def __init__(self, settings: Settings, transporte: TransporteEmail | None = None) -> None:
        self._settings = settings
        self._transporte = transporte if transporte is not None else construir_transporte(settings)

    async def notificar(self, nombre: str, email: str, telefono: str | None) -> ResultadoBienvenida:
        return ResultadoBienvenida(correo=await self._correo(nombre, email), sms=await self._sms(telefono))

    async def _correo(self, nombre: str, email: str) -> str:
        if self._transporte is None:
            return NO_CONFIGURADO
        try:
            await self._transporte.enviar(componer_correo(self._settings, nombre, email))
        except ErrorEnvioCorreo:
            logger.exception("No se pudo enviar el correo de bienvenida.")  # sin el destinatario en el log
            return FALLIDO
        return ENVIADO

    async def _sms(self, telefono: str | None) -> str:
        if not telefono:
            return SIN_TELEFONO
        if not self._settings.sms_enabled:
            return NO_CONFIGURADO
        servicio = obtener_notificacion_service(self._settings)
        enviado = await servicio.enviar_sms_a(telefono, componer_sms(self._settings), "bienvenida")
        return ENVIADO if enviado else FALLIDO
