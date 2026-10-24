"""HU-53: transportes del aviso por correo.

Dos implementaciones con la misma interfaz:

- `SmtpTransporte`: SSL implícito (465) o STARTTLS (587). `smtplib` es
  bloqueante, así que corre en un hilo para no frenar el event loop.
- `HttpApiTransporte`: proveedor transaccional por HTTPS con la API de Resend
  (`POST /emails`, `Authorization: Bearer`). Para plataformas que restringen el
  SMTP saliente.

Un transporte NO traga errores: lanza `ErrorEnvioCorreo` y deja la decisión
(reintentar, auditar el fallo) al despachador. Solo declara éxito cuando el
servidor o el proveedor lo confirman.
"""

import asyncio
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

import httpx

from src.infrastructure.config import Settings

TIMEOUT_SEGUNDOS = 10


class ErrorEnvioCorreo(Exception):
    """El proveedor no confirmó el envío."""


@dataclass(frozen=True, slots=True)
class MensajeCorreo:
    remitente: str
    destinatario: str
    asunto: str
    cuerpo: str


class TransporteEmail(Protocol):
    nombre: str

    async def enviar(self, mensaje: MensajeCorreo) -> None: ...


class SmtpTransporte:
    nombre = "smtp"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def enviar(self, mensaje: MensajeCorreo) -> None:
        correo = EmailMessage()
        correo["Subject"] = mensaje.asunto
        correo["From"] = mensaje.remitente
        correo["To"] = mensaje.destinatario
        correo.set_content(mensaje.cuerpo)
        try:
            await asyncio.to_thread(self._enviar_bloqueante, correo)
        except (OSError, smtplib.SMTPException) as exc:
            raise ErrorEnvioCorreo(f"{type(exc).__name__}: {exc}") from exc

    def _enviar_bloqueante(self, correo: EmailMessage) -> None:
        s = self._settings
        if s.smtp_seguridad == "starttls":
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=TIMEOUT_SEGUNDOS) as servidor:
                servidor.starttls()
                servidor.login(s.smtp_user, s.smtp_password)
                servidor.send_message(correo)
        else:
            with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=TIMEOUT_SEGUNDOS) as servidor:
                servidor.login(s.smtp_user, s.smtp_password)
                servidor.send_message(correo)


class HttpApiTransporte:
    nombre = "http_api"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def enviar(self, mensaje: MensajeCorreo) -> None:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SEGUNDOS) as cliente:
                respuesta = await cliente.post(
                    self._settings.email_api_url,
                    headers={"Authorization": f"Bearer {self._settings.email_api_key}"},
                    json={
                        "from": mensaje.remitente,
                        "to": [mensaje.destinatario],
                        "subject": mensaje.asunto,
                        "text": mensaje.cuerpo,
                    },
                )
                respuesta.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # El cuerpo del error del proveedor puede citar la clave o el
            # destinatario: solo se conserva el código HTTP.
            raise ErrorEnvioCorreo(f"HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ErrorEnvioCorreo(f"{type(exc).__name__}") from exc


def construir_transporte(settings: Settings) -> TransporteEmail | None:
    if not settings.correo_habilitado:
        return None
    if settings.email_transporte == "http_api":
        return HttpApiTransporte(settings)
    return SmtpTransporte(settings)
