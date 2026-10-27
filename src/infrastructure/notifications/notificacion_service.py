"""HU-23/HU-53/HU-54: aviso fuera de la aplicación ante una excursión crítica.

Una alerta que solo existe en el dashboard no sirve de noche ni con la farmacia
cerrada, que es cuando una excursión térmica arruina un lote de vacunas.

Reparto de responsabilidades (HU-53):

- `componer_correo_excursion` arma el correo (asunto y cuerpo).
- `transporte_email` lo envía (SMTP o API HTTP) y lanza si el proveedor no lo
  confirma.
- `despachador.DespachadorNotificaciones` decide CUÁNDO: después del COMMIT,
  fuera del consumidor MQTT, una sola vez por episodio y con reintentos
  auditados.
- `NotificacionService` (este módulo) conserva los canales opcionales de
  HU-54 —Telegram y SMS—, que nunca bloquean ni rompen la ingesta: cualquier
  fallo se registra y se traga, con cooldown por dispositivo en memoria.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx

from src.application.use_cases.generar_alerta import AvisoExcursion
from src.application.use_cases.vigilar_sin_datos import AvisoSinDatos
from src.infrastructure.config import Settings
from src.infrastructure.notifications.transporte_email import MensajeCorreo

logger = logging.getLogger("infrastructure.notificaciones")

# Perú no aplica horario de verano: UTC-5 fijo. Se evita zoneinfo porque en
# Windows (desarrollo) no trae base de datos de zonas sin el paquete tzdata.
HORA_PERU = timezone(timedelta(hours=-5), "PET")


def _formatear(instante: datetime) -> str:
    utc = instante if instante.tzinfo is not None else instante.replace(tzinfo=timezone.utc)
    local = utc.astimezone(HORA_PERU)
    return f"{local:%d/%m/%Y %H:%M:%S} (hora de Perú) — {utc.astimezone(timezone.utc):%Y-%m-%d %H:%M:%S} UTC"


def componer_correo_excursion(
    aviso: AvisoExcursion,
    remitente: str,
    destinatario: str,
    frontend_url: str = "",
    dispositivo: dict | None = None,
) -> MensajeCorreo:
    """HU-53 criterio 6: el correo debe permitir actuar sin abrir el sistema
    —qué refrigerador, dónde, a cuánto y cuándo— y llevar a la alerta."""
    dispositivo = dispositivo or {}
    temperatura = f"{aviso.temperatura:.1f} °C" if aviso.temperatura is not None else "sin dato de sensor"
    etiqueta = " [DIFERIDA]" if aviso.diferida else ""
    lineas = [
        "ALERTA CRÍTICA — Cadena de frío",
        "",
        f"Dispositivo: {aviso.device_id}",
    ]
    if dispositivo.get("nombre"):
        lineas.append(f"Equipo: {dispositivo['nombre']}")
    if dispositivo.get("ubicacion"):
        lineas.append(f"Ubicación: {dispositivo['ubicacion']}")
    lineas += [
        f"Temperatura registrada: {temperatura}",
        f"Momento de la lectura: {_formatear(aviso.captured_at)}",
    ]
    if aviso.diferida:
        recibida = _formatear(aviso.received_at) if aviso.received_at else "—"
        lineas += [
            f"Recibida por el sistema: {recibida}",
            "",
            "Esta lectura llegó DIFERIDA (el nodo estuvo sin conexión y la envió "
            "desde su memoria). La excursión pudo haber ocurrido antes de este aviso.",
        ]
    lineas += [
        "",
        "La temperatura salió del rango 2–8 °C exigido para medicamentos termolábiles. "
        "Verifique el refrigerador de inmediato y registre la acción correctiva en el sistema.",
    ]
    if frontend_url:
        lineas += ["", f"Ver la alerta: {frontend_url.rstrip('/')}/alertas?id={aviso.alerta_id}"]
    return MensajeCorreo(
        remitente=remitente,
        destinatario=destinatario,
        asunto=f"[ColdCare] EXCURSIÓN CRÍTICA{etiqueta} — {aviso.device_id}",
        cuerpo="\n".join(lineas),
    )


def _correo_retencion(
    aviso: AvisoSinDatos, remitente: str, destinatario: str, frontend_url: str, dispositivo: dict
) -> MensajeCorreo:
    """HU-44: rotación por compromiso. El monitoreo queda suspendido (la
    telemetría se retiene) o una lectura retenida salió de rango y su origen no
    se puede comprobar."""
    detalle = aviso.detalle or {}
    if aviso.tipo == "excursion_no_verificada":
        titulo = "EXCURSIÓN CRÍTICA (origen no verificado)"
        temperatura = detalle.get("temperatura_interna")
        lineas = [f"{titulo} — Cadena de frío", "", f"Dispositivo: {aviso.device_id}"]
        if dispositivo.get("nombre"):
            lineas.append(f"Equipo: {dispositivo['nombre']}")
        captura = detalle.get("captured_at")
        lineas += [
            f"Temperatura interior: {temperatura:.2f} °C (fuera de 2-8 °C)" if temperatura is not None
            else "Temperatura interior: —",
            f"Capturada: {captura or 'sin hora sincronizada'}",
            "",
            "La lectura llegó durante una rotación de credencial por compromiso: puede venir del nodo o de "
            "quien tenga el token filtrado, por eso no se registra todavía. Compruebe ya el refrigerador con "
            "el termómetro de respaldo y actúe como ante una excursión real.",
        ]
    else:
        titulo = "MONITOREO SUSPENDIDO POR ROTACIÓN"
        lineas = [
            f"{titulo} — Cadena de frío", "", f"Dispositivo: {aviso.device_id}", "",
            "Se rotó la credencial del nodo por sospecha de compromiso. Hasta que el nodo demuestre que tiene "
            "la credencial nueva, el sistema retiene su telemetría (el nodo la conserva) y solo vigila "
            "excursiones con datos de origen no verificado.",
            "Cambie la contraseña en la consola del broker, confirme en el sistema y, mientras tanto, controle "
            "el refrigerador con el termómetro de respaldo.",
        ]
    if frontend_url:
        lineas += ["", f"Ver el estado del nodo: {frontend_url.rstrip('/')}/dispositivos?id={aviso.device_id}"]
    return MensajeCorreo(
        remitente=remitente,
        destinatario=destinatario,
        asunto=f"[ColdCare] {titulo} — {aviso.device_id}",
        cuerpo="\n".join(lineas),
    )


def componer_correo_sin_datos(
    aviso: AvisoSinDatos,
    remitente: str,
    destinatario: str,
    frontend_url: str = "",
    dispositivo: dict | None = None,
) -> MensajeCorreo:
    """HU-54: el nodo dejó de reportar. No es una excursión: es no saber qué
    temperatura hay dentro del refrigerador."""
    dispositivo = dispositivo or {}
    if aviso.tipo != "sin_datos":
        return _correo_retencion(aviso, remitente, destinatario, frontend_url, dispositivo)
    lineas = ["SIN DATOS — Cadena de frío", "", f"Dispositivo: {aviso.device_id}"]
    if dispositivo.get("nombre"):
        lineas.append(f"Equipo: {dispositivo['nombre']}")
    if dispositivo.get("ubicacion"):
        lineas.append(f"Ubicación: {dispositivo['ubicacion']}")
    ultimo = _formatear(aviso.ultimo_reporte_en) if aviso.ultimo_reporte_en else "—"
    lineas += [
        f"Último reporte recibido: {ultimo}",
        f"Silencio tolerado: {aviso.umbral_segundos} s",
        "",
        "El nodo no envía lecturas. Mientras siga así no hay registro de la temperatura del "
        "refrigerador: revise la alimentación y la red del nodo y, si hace falta, registre "
        "lecturas manuales con el termómetro de respaldo.",
    ]
    if frontend_url:
        lineas += ["", f"Ver el estado del nodo: {frontend_url.rstrip('/')}/dispositivos?id={aviso.device_id}"]
    return MensajeCorreo(
        remitente=remitente,
        destinatario=destinatario,
        asunto=f"[ColdCare] SIN DATOS — {aviso.device_id}",
        cuerpo="\n".join(lineas),
    )


class NotificacionService:
    """HU-54: canales opcionales (Telegram y SMS). El correo NO pasa por aquí.

    Devuelven el resultado del SMS para que el despachador lo audite:
    "enviado", "fallido", "sin_telefono", "cooldown", o None si el canal SMS
    está deshabilitado."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._ultimo_aviso: dict[str, datetime] = {}
        self._cooldown = timedelta(minutes=settings.notificacion_cooldown_minutos)

    @property
    def habilitado(self) -> bool:
        return self._settings.telegram_enabled or self._settings.sms_enabled

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
        telefono_destino: str | None = None,
        origen_no_verificado: bool = False,
    ) -> str | None:
        """El SMS, al ser un canal explícitamente opcional (HU-54) y personal,
        no tiene destinatario global: sin número registrado, no se envía.

        HU-44: `origen_no_verificado` marca una lectura recibida durante una
        rotación por compromiso (puede venir de quien tiene el token filtrado)."""
        if not self.habilitado:
            return None
        if not self._debe_notificar(device_id):
            return "cooldown" if self._settings.sms_enabled else None

        temp_texto = f"{temperatura:.1f} °C" if temperatura is not None else "sin dato de sensor"
        if self._settings.telegram_enabled:
            await self._enviar_telegram(device_id, temp_texto, timestamp)
        if not self._settings.sms_enabled:
            return None
        if not telefono_destino:
            return "sin_telefono"
        if origen_no_verificado:
            texto = (
                f"[ColdCare] EXCURSION CRITICA (ORIGEN NO VERIFICADO) {device_id}: {temp_texto} a las "
                f"{timestamp}. Llego durante una rotacion de credencial: compruebe el refrigerador con "
                "el termometro de respaldo."
            )
        else:
            texto = (
                f"[ColdCare] EXCURSION CRITICA {device_id}: {temp_texto} a las {timestamp}. "
                "Verifique el refrigerador de inmediato."
            )
        return "enviado" if await self._enviar_sms(device_id, texto, telefono_destino) else "fallido"

    async def notificar_sin_datos(
        self, device_id: str, ultimo_reporte: str | None, telefono_destino: str | None = None
    ) -> str | None:
        """HU-54: mismo canal y mismo cooldown por dispositivo que la excursión:
        un nodo que se apaga y además registra una excursión no manda dos SMS
        seguidos."""
        if not self._settings.sms_enabled:
            return None
        if not telefono_destino:
            return "sin_telefono"
        if not self._debe_notificar(device_id):
            return "cooldown"
        texto = (
            f"[ColdCare] SIN DATOS {device_id}: el nodo no reporta desde {ultimo_reporte or 'hace rato'}. "
            "Revise alimentacion y red del nodo."
        )
        return "enviado" if await self._enviar_sms(device_id, texto, telefono_destino) else "fallido"

    # ── Canales ───────────────────────────────────────────────────────────
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

    async def _enviar_sms(self, device_id: str, texto: str, telefono_destino: str) -> bool:
        """HU-54: pasarela compatible con la API REST de Twilio (Account SID
        + Auth Token vía HTTP Basic, cuerpo form-encoded) — el proveedor
        concreto es responsabilidad de configuración/despliegue (Settings),
        no de este servicio. No declara envío exitoso salvo que el proveedor
        confirme con 2xx (HU-54 criterio 2: "sin declarar entrega exitosa si
        ningún proveedor la confirma")."""
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
            return True
        except Exception:
            logger.exception("No se pudo enviar el aviso por SMS para %s", device_id)
            return False


_instancia: NotificacionService | None = None


def obtener_notificacion_service(settings: Settings) -> NotificacionService:
    """BE-018: una sola instancia por proceso y configuración.

    El antiflood por dispositivo vive en el estado de la instancia. Crear una
    por cada lectura (como hacían el consumidor MQTT y la ingesta REST) dejaba
    el cooldown siempre vacío: cada reapertura de episodio volvía a notificar.
    `get_settings()` está cacheado, así que la instancia se conserva mientras
    la configuración no cambie."""
    global _instancia
    if _instancia is None or _instancia._settings is not settings:
        _instancia = NotificacionService(settings)
    return _instancia
