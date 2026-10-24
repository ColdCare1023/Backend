import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from src.domain.entities.alerta_termica import AlertaTermica
from src.domain.repositories.i_alerta_repository import IAlertaRepository
from src.domain.value_objects.nivel_riesgo import NivelRiesgo

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

# HU-53 (P4): una lectura que llega más de esto después de capturada viene del
# buffer offline del nodo: el aviso debe decir que la excursión es diferida.
RETRASO_LECTURA_DIFERIDA = timedelta(minutes=5)


def _a_utc(valor: datetime) -> datetime:
    return valor if valor.tzinfo is not None else valor.replace(tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class AvisoExcursion:
    """HU-53: aviso pendiente de una apertura (o reapertura) de episodio
    crítico. El caso de uso solo lo DECIDE; quien hace COMMIT lo entrega al
    despachador después (D2), nunca antes ni dentro de la transacción."""

    alerta_id: UUID
    device_id: str
    temperatura: float | None
    captured_at: datetime
    received_at: datetime | None = None
    time_quality: str | None = None

    @property
    def diferida(self) -> bool:
        if self.time_quality is not None and self.time_quality != "synced":
            return True
        if self.received_at is None:
            return False
        return _a_utc(self.received_at) - _a_utc(self.captured_at) > RETRASO_LECTURA_DIFERIDA


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

    HU-53: la apertura o reapertura de un episodio crítico deja un
    `AvisoExcursion` en `avisos_pendientes`. Este caso de uso no envía nada:
    corre dentro de la transacción de la lectura, y un correo enviado aquí
    saldría aunque el COMMIT fallara después.
    """

    def __init__(
        self,
        alerta_repository: IAlertaRepository,
        ventana_normalizacion_minutos: int = COOLDOWN_MINUTOS,
    ) -> None:
        self._alerta_repository = alerta_repository
        self._ventana_normalizacion_minutos = ventana_normalizacion_minutos
        self.avisos_pendientes: list[AvisoExcursion] = []

    def _registrar_aviso_si_es_apertura_critica(
        self,
        alerta: AlertaTermica | None,
        episodio_previo: AlertaTermica | None,
        temperatura: float | None,
        timestamp: datetime,
        received_at: datetime | None,
        time_quality: str | None,
    ) -> None:
        """Solo al ABRIR (o reabrir) un episodio crítico, no en cada lectura del
        mismo episodio. Una reapertura dentro de la ventana conserva el id de la
        alerta: el despachador la descarta si ya fue notificada (P3)."""
        if alerta is None or alerta.id is None:
            return
        if alerta.nivel_riesgo != NivelRiesgo.EXCURSION_CRITICA:
            return
        # Ya había un episodio crítico abierto: es continuación, no un evento nuevo.
        if episodio_previo is not None and episodio_previo.nivel_riesgo == NivelRiesgo.EXCURSION_CRITICA:
            return
        self.avisos_pendientes.append(
            AvisoExcursion(
                alerta_id=alerta.id,
                device_id=alerta.device_id,
                temperatura=temperatura,
                captured_at=_a_utc(timestamp),
                received_at=received_at,
                time_quality=time_quality,
            )
        )

    async def execute(
        self,
        reading_id: UUID,
        device_id: str,
        nivel_riesgo: NivelRiesgo | None,
        timestamp: datetime,
        temperatura_interna: float | None = None,
        received_at: datetime | None = None,
        time_quality: str | None = None,
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
            self._registrar_aviso_si_es_apertura_critica(
                reabierta, episodio_abierto, temperatura_interna, timestamp, received_at, time_quality
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
        self._registrar_aviso_si_es_apertura_critica(
            creada, episodio_abierto, temperatura_interna, timestamp, received_at, time_quality
        )
        return creada
