import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from src.domain.entities.alerta_termica import AlertaTermica
from src.domain.repositories.i_alerta_repository import IAlertaRepository
from src.domain.value_objects.nivel_riesgo import NivelRiesgo

if TYPE_CHECKING:
    from src.domain.repositories.i_device_repository import IDeviceRepository
    from src.infrastructure.notifications.notificacion_service import NotificacionService

logger = logging.getLogger("application.generar_alerta")

MENSAJES_POR_RIESGO = {
    NivelRiesgo.RIESGO_PREVENTIVO: "Riesgo preventivo: la temperatura se acerca al límite del rango 2-8 C.",
    NivelRiesgo.EXCURSION_CRITICA: "Excursión crítica: la temperatura está fuera del rango 2-8 C.",
}

# AIV-02 / HU-20: ventana de normalización (cooldown) — un episodio recién
# cerrado del mismo dispositivo y tipo de riesgo se reabre (no se crea uno
# nuevo) si la siguiente lectura crítica llega dentro de esta ventana,
# evitando flapping de apertura/cierre por una única lectura normal aislada
# (histéresis). Valor por defecto; configurable por instancia via Settings
# (ver GenerarAlertaUseCase.__init__ y su construcción en los routers).
COOLDOWN_MINUTOS = 15


def _a_utc(valor: datetime) -> datetime:
    return valor if valor.tzinfo is not None else valor.replace(tzinfo=timezone.utc)


class GenerarAlertaUseCase:
    """RF-09: gestiona el EPISODIO de alerta por dispositivo+tipo de riesgo,
    no una alerta por lectura (corrige AIV-02, "tormenta de alertas").

    Reglas:
    - Una lectura NORMAL cierra el episodio abierto del dispositivo (evento
      de recuperación), sin crear ninguna fila nueva.
    - Una lectura crítica del MISMO tipo que el episodio ya abierto solo
      actualiza ese episodio (lectura más reciente + timestamp).
    - Una lectura crítica de OTRO tipo (escalamiento/desescalamiento) cierra
      el episodio anterior y abre uno nuevo.
    - Sin episodio abierto: si el último episodio CERRADO del mismo
      dispositivo+tipo terminó hace menos de COOLDOWN_MINUTOS, se reabre en
      vez de crear una fila nueva. Si no, se crea una alerta nueva.
    """

    def __init__(
        self,
        alerta_repository: IAlertaRepository,
        notificacion_service: "NotificacionService | None" = None,
        ventana_normalizacion_minutos: int = COOLDOWN_MINUTOS,
        device_repository: "IDeviceRepository | None" = None,
    ) -> None:
        self._alerta_repository = alerta_repository
        self._notificacion_service = notificacion_service
        self._ventana_normalizacion_minutos = ventana_normalizacion_minutos
        # HU-53/HU-54: resuelve el responsable registrado del dispositivo
        # para notificar a su contacto propio en vez de uno global fijo.
        # None es válido (algunos llamadores no lo inyectan): se notifica
        # igual, cayendo al destinatario global de correo (ver
        # NotificacionService.notificar_excursion_critica) y sin SMS.
        self._device_repository = device_repository

    async def _avisar_si_es_apertura_critica(
        self,
        alerta: AlertaTermica | None,
        episodio_previo: AlertaTermica | None,
        temperatura: float | None,
        timestamp: datetime,
    ) -> None:
        """HU-23: solo se avisa al ABRIR (o reabrir) un episodio crítico, no en
        cada lectura del mismo episodio. El servicio aplica además su propio
        cooldown por dispositivo."""
        if self._notificacion_service is None or alerta is None:
            return
        if alerta.nivel_riesgo != NivelRiesgo.EXCURSION_CRITICA:
            return
        # Ya había un episodio crítico abierto: es continuación, no un evento nuevo.
        if episodio_previo is not None and episodio_previo.nivel_riesgo == NivelRiesgo.EXCURSION_CRITICA:
            return
        email_destino: str | None = None
        telefono_destino: str | None = None
        if self._device_repository is not None:
            dispositivo = await self._device_repository.obtener(alerta.device_id)
            if dispositivo is not None:
                email_destino = dispositivo.get("responsable_email")
                telefono_destino = dispositivo.get("responsable_telefono")
        try:
            await self._notificacion_service.notificar_excursion_critica(
                device_id=alerta.device_id,
                temperatura=temperatura,
                timestamp=_a_utc(timestamp).isoformat(),
                email_destino=email_destino,
                telefono_destino=telefono_destino,
            )
        except Exception:  # pragma: no cover - defensa: notificar nunca rompe la ingesta
            logger.exception("Fallo al notificar la excursión crítica de %s", alerta.device_id)

    async def execute(
        self,
        reading_id: UUID,
        device_id: str,
        nivel_riesgo: NivelRiesgo | None,
        timestamp: datetime,
        temperatura_interna: float | None = None,
    ) -> AlertaTermica | None:
        episodio_abierto = await self._alerta_repository.obtener_episodio_abierto(device_id)

        if nivel_riesgo is None or nivel_riesgo == NivelRiesgo.NORMAL:
            if episodio_abierto is not None:
                episodio_abierto.cerrar(timestamp)
                await self._alerta_repository.actualizar(episodio_abierto)
            return None

        if episodio_abierto is not None:
            if episodio_abierto.nivel_riesgo == nivel_riesgo:
                episodio_abierto.registrar_lectura(reading_id, timestamp)
                return await self._alerta_repository.actualizar(episodio_abierto)
            # Escalamiento/desescalamiento: cierra el episodio anterior antes
            # de evaluar si corresponde abrir/reabrir uno del nuevo tipo.
            episodio_abierto.cerrar(timestamp)
            await self._alerta_repository.actualizar(episodio_abierto)

        ultimo_cerrado = await self._alerta_repository.obtener_ultimo_cerrado(device_id, nivel_riesgo)
        if (
            ultimo_cerrado is not None
            and ultimo_cerrado.cerrada_en is not None
            and (_a_utc(timestamp) - _a_utc(ultimo_cerrado.cerrada_en))
            <= timedelta(minutes=self._ventana_normalizacion_minutos)
        ):
            ultimo_cerrado.reabrir(reading_id, timestamp)
            reabierta = await self._alerta_repository.actualizar(ultimo_cerrado)
            await self._avisar_si_es_apertura_critica(
                reabierta, episodio_abierto, temperatura_interna, timestamp
            )
            return reabierta

        alerta = AlertaTermica(
            reading_id=reading_id,
            device_id=device_id,
            nivel_riesgo=nivel_riesgo,
            mensaje=MENSAJES_POR_RIESGO[nivel_riesgo],
            lectura_inicial_id=reading_id,
            lectura_mas_reciente_id=reading_id,
            ultima_actualizacion=timestamp,
        )
        creada = await self._alerta_repository.agregar(alerta)
        await self._avisar_si_es_apertura_critica(
            creada, episodio_abierto, temperatura_interna, timestamp
        )
        return creada
