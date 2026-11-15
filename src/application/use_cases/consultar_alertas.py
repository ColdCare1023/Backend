from datetime import datetime, timezone
from uuid import UUID

from src.domain.entities.accion_correctiva import AccionCorrectiva
from src.domain.entities.alerta_termica import AlertaTermica
from src.domain.exceptions import DomainError, RecursoNoEncontradoError
from src.domain.repositories.i_accion_correctiva_repository import IAccionCorrectivaRepository
from src.domain.repositories.i_alerta_repository import IAlertaRepository
from src.domain.repositories.i_trazabilidad_repository import ITrazabilidadRepository


class ConsultarAlertasUseCase:
    def __init__(self, alerta_repository: IAlertaRepository) -> None:
        self._alerta_repository = alerta_repository

    async def execute(
        self,
        device_id: str | None = None,
        revisada: bool | None = None,
        estado: str | None = None,
        limite: int = 100,
        offset: int = 0,
    ) -> list[AlertaTermica]:
        return await self._alerta_repository.listar(
            device_id=device_id, revisada=revisada, estado=estado, limite=limite, offset=offset
        )


class MarcarAlertaRevisadaUseCase:
    """HU-23: reconocimiento de una alerta crítica (PENDIENTE -> RECONOCIDA).
    Conserva el nombre histórico (el endpoint sigue siendo /revisar) pero
    aplica la máquina de estados completa, no solo el booleano `revisada`.

    HU-23 esc. 3 (concurrencia): la transición es un UPDATE atómico
    `... WHERE estado = 'pendiente'` (`reconocer_si_pendiente`), no un
    leer-modificar-escribir. Si una acción correctiva concurrente ya la
    reconoció y atendió, este reconocimiento no encuentra la alerta PENDIENTE
    y se rechaza (409) en vez de devolverla de ATENDIDA a RECONOCIDA.

    Con `trazabilidad_repository` toma antes el candado de la cadena del
    dispositivo (mismo orden que la ingesta: cadena, luego alerta)."""

    def __init__(
        self,
        alerta_repository: IAlertaRepository,
        trazabilidad_repository: ITrazabilidadRepository | None = None,
    ) -> None:
        self._alerta_repository = alerta_repository
        self._trazabilidad_repository = trazabilidad_repository

    async def execute(self, alerta_id: UUID, usuario_id: UUID) -> AlertaTermica:
        alerta = await self._alerta_repository.obtener_por_id(alerta_id)
        if alerta is None:
            raise RecursoNoEncontradoError(f"Alerta {alerta_id} no encontrada")
        if self._trazabilidad_repository is not None:
            await self._trazabilidad_repository.bloquear_cadena(alerta.device_id)
        reconocida = await self._alerta_repository.reconocer_si_pendiente(
            alerta_id, usuario_id, datetime.now(tz=timezone.utc)
        )
        actual = await self._alerta_repository.obtener_por_id(alerta_id, refrescar=True)
        if not reconocida:
            estado = actual.estado.value if actual is not None else alerta.estado.value
            raise DomainError(f"La alerta ya fue reconocida o atendida (estado actual: {estado})")
        if actual is None:  # pragma: no cover - borrada entre el UPDATE y la relectura
            raise RecursoNoEncontradoError(f"Alerta {alerta_id} no encontrada")
        return actual


class ConsultarAccionesCorrectivasUseCase:
    """HU-23 criterio 4: acciones correctivas de una alerta en orden
    cronológico, para reconstruir su ciclo de atención (decisión, acción,
    justificación, responsable y timestamp) — incluidas sus rectificaciones
    (HU-28), que referencian a la acción que corrigen en vez de sobrescribirla."""

    def __init__(
        self, alerta_repository: IAlertaRepository, accion_repository: IAccionCorrectivaRepository
    ) -> None:
        self._alerta_repository = alerta_repository
        self._accion_repository = accion_repository

    async def execute(self, alerta_id: UUID) -> list[AccionCorrectiva]:
        alerta = await self._alerta_repository.obtener_por_id(alerta_id)
        if alerta is None:
            raise RecursoNoEncontradoError(f"Alerta {alerta_id} no encontrada")
        return await self._accion_repository.listar_por_alerta(alerta_id)
