"""HU-53: transportes del aviso por correo.

Tres implementaciones con la misma interfaz:

- `SmtpTransporte`: SSL implícito (465) o STARTTLS (587). `smtplib` es
  bloqueante, así que corre en un hilo para no frenar el event loop.
- `HttpApiTransporte`: proveedor transaccional por HTTPS con la API de Resend
  (`POST /emails`, `Authorization: Bearer`). Para plataformas que restringen el
  SMTP saliente.
- `AppsScriptTransporte`: relé propio publicado como aplicación web de Google
  Apps Script (POST JSON con un secreto compartido en el cuerpo, un
  destinatario por petición). Envía con la cuenta de Gmail/Workspace que
  publica el script, sin dominio verificado.

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
# Apps Script arranca en frío y responde con una redirección: margen mayor.
TIMEOUT_APPS_SCRIPT_SEGUNDOS = 15


class ErrorEnvioCorreo(Exception):
    """El proveedor no confirmó el envío."""


@dataclass(frozen=True, slots=True)
class MensajeCorreo:
    remitente: str
    # Uno o varios correos separados por coma (EMAIL_DESTINO_RESPALDO=a@x.com,b@y.com).
    destinatario: str
    asunto: str
    cuerpo: str
    # Versión HTML opcional: el cuerpo de texto sigue siendo la alternativa para clientes
    # que no la muestran (multipart/alternative) y es lo que ve un lector de pantalla.
    html: str | None = None


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
        if mensaje.html:
            correo.add_alternative(mensaje.html, subtype="html")
        try:
            await asyncio.to_thread(self._enviar_bloqueante, correo)
        except (OSError, smtplib.SMTPException) as exc:
            raise ErrorEnvioCorreo(f"{type(exc).__name__}: {exc}") from exc

    def _enviar_bloqueante(self, correo: EmailMessage) -> None:
        s = self._settings
        if s.smtp_seguridad == "ninguna":
            # Mailpit / SMTP de prueba: en claro y sin login (no admitido en producción).
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=TIMEOUT_SEGUNDOS) as servidor:
                servidor.send_message(correo)
        elif s.smtp_seguridad == "starttls":
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
                        "to": [d.strip() for d in mensaje.destinatario.split(",") if d.strip()],
                        "subject": mensaje.asunto,
                        "text": mensaje.cuerpo,
                        **({"html": mensaje.html} if mensaje.html else {}),
                    },
                )
                respuesta.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # El cuerpo del error del proveedor puede citar la clave o el
            # destinatario: solo se conserva el código HTTP.
            raise ErrorEnvioCorreo(f"HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ErrorEnvioCorreo(f"{type(exc).__name__}") from exc


# Único destino admitido para la redirección de Apps Script: el POST a
# script.google.com responde 302 hacia aquí con el resultado.
_HOST_REDIRECCION_APPS_SCRIPT = "script.googleusercontent.com"


class AppsScriptTransporte:
    """Relé HTTPS de Google Apps Script.

    Contrato: `POST EMAIL_API_URL` con JSON
    `{"secreto", "to", "subject", "text", "html", "nombre_remitente"}` y un solo
    destinatario en `to` (varios destinatarios son varias peticiones: el relé
    rechaza más de uno). Solo es éxito un HTTP 200 cuyo cuerpo sea JSON con
    `"ok": true`; cualquier otra cosa (incluida una página de inicio de sesión
    de Google, que llega con 200 y HTML) es un fallo.

    Apps Script contesta el POST con una redirección 302 a
    `script.googleusercontent.com`. No se usa `follow_redirects` de httpx (con
    307/308 reenviaría el cuerpo, y con él el secreto, a cualquier host): se
    sigue a mano una sola redirección 302/303, con GET y sin cuerpo, y solo si
    va por HTTPS a ese host. Cualquier otra redirección es un fallo.

    El secreto viaja en el cuerpo porque Apps Script no expone las cabeceras de
    la petición al script. Nunca aparece en logs ni en el mensaje de error.
    """

    nombre = "apps_script"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    async def enviar(self, mensaje: MensajeCorreo) -> None:
        destinatarios = [d.strip() for d in mensaje.destinatario.split(",") if d.strip()]
        if not destinatarios:
            raise ErrorEnvioCorreo("Sin destinatario")
        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT_APPS_SCRIPT_SEGUNDOS, follow_redirects=False, transport=self._transport
            ) as cliente:
                for destino in destinatarios:
                    await self._enviar_a(cliente, destino, mensaje)
        except httpx.HTTPError as exc:
            raise ErrorEnvioCorreo(f"{type(exc).__name__}") from exc

    async def _enviar_a(self, cliente: httpx.AsyncClient, destino: str, mensaje: MensajeCorreo) -> None:
        s = self._settings
        cuerpo: dict[str, object] = {
            "secreto": s.email_api_key,
            "to": [destino],
            "subject": mensaje.asunto,
            "text": mensaje.cuerpo,
            "nombre_remitente": s.email_from_nombre or "ColdCare",
        }
        if mensaje.html:
            cuerpo["html"] = mensaje.html
        respuesta = await cliente.post(s.email_api_url, json=cuerpo)
        if respuesta.status_code in (302, 303):
            siguiente = respuesta.url.join(respuesta.headers.get("location", ""))
            if siguiente.scheme != "https" or siguiente.host != _HOST_REDIRECCION_APPS_SCRIPT:
                raise ErrorEnvioCorreo("Apps Script redirigió a un destino no admitido")
            respuesta = await cliente.get(siguiente)
        if respuesta.status_code != 200:
            raise ErrorEnvioCorreo(f"HTTP {respuesta.status_code}")
        try:
            datos = respuesta.json()
        except ValueError as exc:
            raise ErrorEnvioCorreo("Apps Script respondió sin JSON") from exc
        if not (isinstance(datos, dict) and datos.get("ok") is True):
            # El detalle del relé puede citar el cuerpo recibido: no se copia.
            raise ErrorEnvioCorreo("Apps Script no confirmó el envío")


def construir_transporte(settings: Settings) -> TransporteEmail | None:
    if not settings.correo_habilitado:
        return None
    if settings.email_transporte == "http_api":
        return HttpApiTransporte(settings)
    if settings.email_transporte == "apps_script":
        return AppsScriptTransporte(settings)
    return SmtpTransporte(settings)
