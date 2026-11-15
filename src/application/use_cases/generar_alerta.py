import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from src.domain.entities.alerta_termica import AlertaTermica
from src.domain.repositories.i_alerta_repository import IAlertaRepository
from src.domain.value_objects.estado_alerta import EstadoAlerta
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
# (histéresis). Nunca se reabre un episodio ATENDIDO (HU-21 esc. 3). Valor por
# defecto; configurable por instancia via Settings (ver
# GenerarAlertaUseCase.__init__ y su construcción en los routers).
COOLDOWN_MINUTOS = 15

# HU-20 esc. 3: un episodio PREVENTIVO no se resuelve con la primera lectura
# Normal, sino cuando el riesgo lleva esta ventana completa en Normal sin
# interrupción (una lectura preventiva la reinicia; una crítica escala). 0 =
# comportamiento anterior (cierre inmediato). La excursión crítica (HU-21 esc.
# 2) sigue cerrándose con la primera lectura válida en rango.
MINUTOS_NORMALES_RESOLVER_PREVENTIVA = 10

# Transiciones del episodio que quedan en la cadena (HU-20 esc. 4).
TRANSICION_CREACION = "creacion"
TRANSICION_ACTUALIZACION = "actualizacion"
TRANSICION_REAPERTURA = "reapertura"
TRANSICION_NORMALIZACION = "normalizacion_en_curso"
# Motivo del cierre (payload de ALERTA_CERRADA).
CIERRE_RECUPERACION = "recuperacion"
CIERRE_NORMALIZACION = "ventana_normalizacion_cumplida"
CIERRE_CAMBIO_NIVEL = "cambio_de_nivel"

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
    # `riesgo_preventivo` = a punto de salirse del rango; `excursion_critica` = ya se salió.
    nivel: NivelRiesgo = NivelRiesgo.EXCURSION_CRITICA

    @property
    def es_preventivo(self) -> bool:
        return self.nivel == NivelRiesgo.RIESGO_PREVENTIVO

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
    - Una lectura NORMAL cierra el episodio CRÍTICO abierto del dispositivo
      (evento de recuperación), sin crear ninguna fila nueva. Un episodio
      PREVENTIVO entra en normalización y se cierra cuando el riesgo lleva
      `minutos_normales_preventiva` seguidos en Normal (HU-20 esc. 3).
    - Una lectura crítica del MISMO tipo que el episodio ya abierto solo
      actualiza ese episodio (lectura más reciente + timestamp).
    - Una lectura crítica de OTRO tipo (escalamiento/desescalamiento) cierra
      el episodio anterior y abre uno nuevo.
    - Sin episodio abierto: si el último episodio CERRADO del mismo
      dispositivo+tipo terminó hace menos de COOLDOWN_MINUTOS y todavía no
      fue ATENDIDO, se reabre en vez de crear una fila nueva. Si no (fuera de
      la ventana, o ya atendido: HU-21 esc. 3 / HU-23 esc. 3), se crea una
      alerta nueva PENDIENTE, que se avisa.

    HU-53: la apertura o reapertura de un episodio crítico deja un
    `AvisoExcursion` en `avisos_pendientes`. Este caso de uso no envía nada:
    corre dentro de la transacción de la lectura, y un correo enviado aquí
    saldría aunque el COMMIT fallara después.
    """

    def __init__(
        self,
        alerta_repository: IAlertaRepository,
        ventana_normalizacion_minutos: int = COOLDOWN_MINUTOS,
        minutos_normales_preventiva: int = MINUTOS_NORMALES_RESOLVER_PREVENTIVA,
    ) -> None:
        self._alerta_repository = alerta_repository
        self._ventana_normalizacion_minutos = ventana_normalizacion_minutos
        self._minutos_normales_preventiva = minutos_normales_preventiva
        self.avisos_pendientes: list[AvisoExcursion] = []
        # Episodios que este caso de uso cerró: quien orquesta deja el evento
        # ALERTA_CERRADA en la cadena (el cierre no devuelve la alerta).
        self.cierres: list[AlertaTermica] = []
        # Por qué se cerró cada episodio de `cierres` (clave: id de la alerta).
        self.motivos_cierre: dict[UUID, str] = {}
        # HU-20 esc. 4: qué le pasó al episodio con la última lectura.
        self.transicion: str | None = None

    @property
    def minutos_normales_preventiva(self) -> int:
        return self._minutos_normales_preventiva

    @staticmethod
    def _es_diferida(timestamp: datetime, received_at: datetime | None) -> bool:
        """Lectura que llegó más de RETRASO_LECTURA_DIFERIDA después de capturada."""
        return received_at is not None and _a_utc(received_at) - _a_utc(timestamp) > RETRASO_LECTURA_DIFERIDA

    async def _cerrar(self, episodio: AlertaTermica, timestamp: datetime, motivo: str = CIERRE_RECUPERACION) -> None:
        episodio.cerrar(timestamp)
        await self._alerta_repository.actualizar(episodio)
        self.cierres.append(episodio)
        if episodio.id is not None:
            self.motivos_cierre[episodio.id] = motivo

    async def _normalizar_o_cerrar(self, episodio: AlertaTermica, timestamp: datetime) -> None:
        """Lectura Normal con un episodio abierto. Crítico: se cierra ya
        (HU-21 esc. 2). Preventivo: cuenta la ventana de normalización y se
        cierra solo al completarla (HU-20 esc. 3)."""
        if episodio.nivel_riesgo != NivelRiesgo.RIESGO_PREVENTIVO or self._minutos_normales_preventiva <= 0:
            await self._cerrar(episodio, timestamp)
            return
        if episodio.iniciar_normalizacion(timestamp):
            await self._alerta_repository.actualizar(episodio)
        if episodio.minutos_en_normalizacion(timestamp) >= self._minutos_normales_preventiva:
            await self._cerrar(episodio, timestamp, CIERRE_NORMALIZACION)
        else:
            self.transicion = TRANSICION_NORMALIZACION

    def _registrar_aviso_si_es_apertura_critica(
        self,
        alerta: AlertaTermica | None,
        episodio_previo: AlertaTermica | None,
        temperatura: float | None,
        timestamp: datetime,
        received_at: datetime | None,
        time_quality: str | None,
    ) -> None:
        """Solo al ABRIR (o reabrir) un episodio —preventivo o crítico—, no en cada
        lectura del mismo episodio. Una reapertura dentro de la ventana conserva
        el id de la alerta: el despachador la descarta si ya fue notificada (P3)."""
        if alerta is None or alerta.id is None:
            return
        if alerta.nivel_riesgo not in (NivelRiesgo.EXCURSION_CRITICA, NivelRiesgo.RIESGO_PREVENTIVO):
            return
        # Ya había un episodio del mismo nivel abierto: es continuación, no un evento nuevo.
        # Pasar de preventivo a crítico SÍ es un evento nuevo (otra alerta, otro aviso).
        if episodio_previo is not None and episodio_previo.nivel_riesgo == alerta.nivel_riesgo:
            return
        self.avisos_pendientes.append(
            AvisoExcursion(
                alerta_id=alerta.id,
                device_id=alerta.device_id,
                temperatura=temperatura,
                captured_at=_a_utc(timestamp),
                received_at=received_at,
                time_quality=time_quality,
                nivel=alerta.nivel_riesgo,
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
        self.transicion = None
        episodio_abierto = await self._alerta_repository.obtener_episodio_abierto(device_id)

        if nivel_riesgo is None or nivel_riesgo == NivelRiesgo.NORMAL:
            if episodio_abierto is not None:
                await self._normalizar_o_cerrar(episodio_abierto, timestamp)
            return None

        if episodio_abierto is not None:
            if episodio_abierto.nivel_riesgo == nivel_riesgo:
                episodio_abierto.registrar_lectura(reading_id, timestamp)
                episodio_abierto.actualizar_pico(temperatura_interna)
                if self._es_diferida(timestamp, received_at):
                    episodio_abierto.historica = True
                self.transicion = TRANSICION_ACTUALIZACION
                return await self._alerta_repository.actualizar(episodio_abierto)
            # Escalamiento/desescalamiento: cierra el episodio anterior antes
            # de evaluar si corresponde abrir/reabrir uno del nuevo tipo.
            await self._cerrar(episodio_abierto, timestamp, CIERRE_CAMBIO_NIVEL)

        ultimo_cerrado = await self._alerta_repository.obtener_ultimo_cerrado(device_id, nivel_riesgo)
        if (
            ultimo_cerrado is not None
            # HU-21 esc. 3 / HU-23 esc. 3: un episodio ATENDIDO cerró su ciclo
            # (reconocimiento + acción correctiva). Reabrirlo escondería la
            # nueva excursión dentro de una alerta «atendida»: fuera de las
            # pendientes del dashboard y sin correo (ya estaba notificada).
            # Se crea una alerta PENDIENTE nueva, que sí se avisa.
            and ultimo_cerrado.estado != EstadoAlerta.ATENDIDA
            and ultimo_cerrado.cerrada_en is not None
            and (_a_utc(timestamp) - _a_utc(ultimo_cerrado.cerrada_en))
            <= timedelta(minutes=self._ventana_normalizacion_minutos)
        ):
            ultimo_cerrado.reabrir(reading_id, timestamp)
            if ultimo_cerrado.iniciada_en is None:
                ultimo_cerrado.iniciada_en = timestamp
            ultimo_cerrado.actualizar_pico(temperatura_interna)
            if self._es_diferida(timestamp, received_at):
                ultimo_cerrado.historica = True
            reabierta = await self._alerta_repository.actualizar(ultimo_cerrado)
            self._registrar_aviso_si_es_apertura_critica(
                reabierta, episodio_abierto, temperatura_interna, timestamp, received_at, time_quality
            )
            self.transicion = TRANSICION_REAPERTURA
            return reabierta

        alerta = AlertaTermica(
            reading_id=reading_id,
            device_id=device_id,
            nivel_riesgo=nivel_riesgo,
            mensaje=MENSAJES_POR_RIESGO[nivel_riesgo],
            lectura_inicial_id=reading_id,
            lectura_mas_reciente_id=reading_id,
            ultima_actualizacion=timestamp,
            # Hora de captura, no de llegada: la lectura puede venir del buffer.
            iniciada_en=timestamp,
            temperatura_pico=temperatura_interna,
            historica=self._es_diferida(timestamp, received_at),
        )
        creada = await self._alerta_repository.agregar(alerta)
        self._registrar_aviso_si_es_apertura_critica(
            creada, episodio_abierto, temperatura_interna, timestamp, received_at, time_quality
        )
        self.transicion = TRANSICION_CREACION
        return creada
