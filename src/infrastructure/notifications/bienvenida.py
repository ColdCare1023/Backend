"""Aviso real al crear una cuenta: correo y SMS al usuario recién registrado.

Usa los mismos transportes de producción que las alertas (SMTP/Resend/Apps
Script para el correo, pasarela compatible con Twilio para el SMS); no hay un
canal de prueba aparte. Nunca incluye una contraseña. Con correo disponible, la
bienvenida lleva un código de activación (48 h) para que el titular elija su
propia contraseña en FRONTEND_URL/recuperar-contrasena. Nunca lanza: cada canal
devuelve su resultado y quien llama lo audita.
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.infrastructure.config import Settings
from src.infrastructure.notifications.correos_cuenta import correo_cuenta_creada
from src.infrastructure.notifications.notificacion_service import obtener_notificacion_service
from src.infrastructure.notifications.transporte_email import (
    ErrorEnvioCorreo,
    MensajeCorreo,
    TransporteEmail,
    construir_transporte,
)

if TYPE_CHECKING:
    from src.application.use_cases.codigos_verificacion import ServicioCodigosVerificacion
    from src.domain.entities.usuario import Usuario

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

    @property
    def correo_configurado(self) -> bool:
        return self._transporte is not None

    async def preparar_activacion(
        self, usuario: "Usuario", codigos: "ServicioCodigosVerificacion", ip: str | None = None
    ) -> MensajeCorreo | None:
        """Emite el código de activación (en la transacción del llamador) y
        compone el correo de bienvenida que lo lleva. None si no hay correo:
        un código que nadie va a recibir no se emite."""
        if self._transporte is None:
            return None
        from src.application.use_cases.gestionar_credenciales import EmitirCodigoActivacionUseCase

        correo = await EmitirCodigoActivacionUseCase(
            codigos, self._settings.url_codigo_cuenta, self._settings.frontend_url
        ).execute(usuario, ip)
        return MensajeCorreo(
            remitente=self._settings.correo_remitente,
            destinatario=correo.destino,
            asunto=correo.asunto,
            cuerpo=correo.cuerpo,
            html=correo.html,
        )

    async def notificar(
        self, nombre: str, email: str, telefono: str | None, activacion: MensajeCorreo | None = None
    ) -> ResultadoBienvenida:
        return ResultadoBienvenida(
            correo=await self._correo(nombre, email, activacion), sms=await self._sms(telefono)
        )

    async def enviar_activacion(self, mensaje: MensajeCorreo) -> str:
        """Solo el correo de activación (sin SMS): lo usa `scripts.asegurar_admins`."""
        return await self._correo("", mensaje.destinatario, mensaje)

    async def _correo(self, nombre: str, email: str, activacion: MensajeCorreo | None = None) -> str:
        if self._transporte is None:
            return NO_CONFIGURADO
        try:
            await self._transporte.enviar(activacion or componer_correo(self._settings, nombre, email))
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
