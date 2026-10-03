"""HU-23: aviso fuera de la aplicación ante una excursión crítica.

Una alerta que solo existe en el dashboard no sirve de noche ni con la farmacia
cerrada, que es cuando una excursión térmica arruina un lote de vacunas. Este
servicio empuja el aviso a un canal que el responsable técnico sí mira.

Dos garantías de diseño:

1. **Nunca bloquea ni rompe la ingesta.** Un SMTP caído o un token de Telegram
   vencido no puede impedir que la lectura se persista y se encadene: cualquier
   fallo se registra y se traga.
2. **Antiflood por dispositivo.** Un episodio crítico produce una lectura cada
   pocos segundos; sin cooldown, el responsable recibiría cientos de correos y
   terminaría silenciando el canal — que es el peor resultado posible.
"""

import asyncio
import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import httpx

from src.infrastructure.config import Settings

logger = logging.getLogger("infrastructure.notificaciones")


class NotificacionService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._ultimo_aviso: dict[str, datetime] = {}
        self._cooldown = timedelta(minutes=settings.notificacion_cooldown_minutos)

    @property
    def habilitado(self) -> bool:
        return self._settings.smtp_enabled or self._settings.telegram_enabled or self._settings.sms_enabled

    def _debe_notificar(self, device_id: str) -> bool:
        ahora = datetime.now(tz=timezone.utc)
        ultimo = self._ultimo_aviso.get(device_id)
        if ultimo is not None and (ahora - ultimo) < self._cooldown:
            logger.debug("Aviso omitido por cooldown para %s", device_id)
            return False
        self._ultimo_aviso[device_id] = ahora
        return True

    async def notificar_excursion_critica(
        self,
        device_id: str,
        temperatura: float | None,
        timestamp: str,
        email_destino: str | None = None,
        telefono_destino: str | None = None,
    ) -> None:
        """HU-53/HU-54: email_destino/telefono_destino son el responsable
        REGISTRADO del dispositivo (devices.responsable_email/telefono). El
        correo cae a SMTP_TO si el dispositivo no tiene uno propio (evita
        dejar una excursión crítica sin avisar a nadie); el SMS, al ser un
        canal explícitamente opcional (HU-54) y personal, NO tiene
        equivalente global: sin número registrado, simplemente no se envía."""
        if not self.habilitado or not self._debe_notificar(device_id):
            return

        temp_texto = f"{temperatura:.1f} °C" if temperatura is not None else "sin dato de sensor"
        if self._settings.smtp_enabled:
            await self._enviar_email(device_id, temp_texto, timestamp, email_destino)
        if self._settings.telegram_enabled:
            await self._enviar_telegram(device_id, temp_texto, timestamp)
        if self._settings.sms_enabled and telefono_destino:
            await self._enviar_sms(device_id, temp_texto, timestamp, telefono_destino)

    # ── Canales ───────────────────────────────────────────────────────────
    async def _enviar_email(
        self, device_id: str, temp_texto: str, timestamp: str, email_destino: str | None = None
    ) -> None:
        mensaje = EmailMessage()
        mensaje["Subject"] = f"[ThermoTrace] EXCURSIÓN CRÍTICA — {device_id}"
        mensaje["From"] = self._settings.smtp_from
        mensaje["To"] = email_destino or self._settings.smtp_to
        mensaje.set_content(
            "ALERTA CRÍTICA — Cadena de frío\n\n"
            f"Dispositivo: {device_id}\n"
            f"Temperatura registrada: {temp_texto}\n"
            f"Momento de la lectura: {timestamp}\n\n"
            "La temperatura salió del rango 2–8 °C exigido para medicamentos "
            "termolábiles. Verifique el refrigerador de inmediato y registre la "
            "acción correctiva en el sistema."
        )
        try:
            # smtplib es bloqueante: se aparta del event loop para no frenar la
            # ingesta de lecturas mientras se negocia la conexión TLS.
            await asyncio.to_thread(self._smtp_enviar, mensaje)
            logger.info("Aviso por email enviado para %s", device_id)
        except Exception:
            logger.exception("No se pudo enviar el aviso por email para %s", device_id)

    def _smtp_enviar(self, mensaje: EmailMessage) -> None:
        with smtplib.SMTP_SSL(
            self._settings.smtp_host, self._settings.smtp_port, timeout=10
        ) as servidor:
            servidor.login(self._settings.smtp_user, self._settings.smtp_password)
            servidor.send_message(mensaje)

    async def _enviar_telegram(self, device_id: str, temp_texto: str, timestamp: str) -> None:
        texto = (
            "🚨 *EXCURSIÓN CRÍTICA — Cadena de frío*\n\n"
            f"Dispositivo: `{device_id}`\n"
            f"Temperatura: *{temp_texto}*\n"
            f"Momento: {timestamp}\n\n"
            "Verifique el refrigerador de inmediato."
        )
        try:
            async with httpx.AsyncClient(timeout=10) as cliente:
                respuesta = await cliente.post(
                    f"https://api.telegram.org/bot{self._settings.telegram_bot_token}/sendMessage",
                    json={
                        "chat_id": self._settings.telegram_chat_id,
                        "text": texto,
                        "parse_mode": "Markdown",
                    },
                )
                respuesta.raise_for_status()
            logger.info("Aviso por Telegram enviado para %s", device_id)
        except Exception:
            logger.exception("No se pudo enviar el aviso por Telegram para %s", device_id)

    async def _enviar_sms(
        self, device_id: str, temp_texto: str, timestamp: str, telefono_destino: str
    ) -> None:
        """HU-54: pasarela compatible con la API REST de Twilio (Account SID
        + Auth Token vía HTTP Basic, cuerpo form-encoded) — el proveedor
        concreto es responsabilidad de configuración/despliegue (Settings),
        no de este servicio. No declara envío exitoso salvo que el proveedor
        confirme con 2xx (HU-54 criterio 2: "sin declarar entrega exitosa si
        ningún proveedor la confirma")."""
        texto = (
            f"[ThermoTrace] EXCURSION CRITICA {device_id}: {temp_texto} a las {timestamp}. "
            "Verifique el refrigerador de inmediato."
        )
        url = self._settings.sms_api_url.format(account_sid=self._settings.sms_account_sid)
        try:
            async with httpx.AsyncClient(timeout=10) as cliente:
                respuesta = await cliente.post(
                    url,
                    auth=(self._settings.sms_account_sid, self._settings.sms_auth_token),
                    data={"From": self._settings.sms_from, "To": telefono_destino, "Body": texto},
                )
                respuesta.raise_for_status()
            logger.info("Aviso por SMS enviado para %s", device_id)
        except Exception:
            logger.exception("No se pudo enviar el aviso por SMS para %s", device_id)
