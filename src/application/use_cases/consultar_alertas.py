from datetime import datetime, timezone
from uuid import UUID

from src.domain.entities.accion_correctiva import AccionCorrectiva
from src.domain.entities.alerta_termica import AlertaTermica
from src.domain.exceptions import RecursoNoEncontradoError
from src.domain.repositories.i_accion_correctiva_repository import IAccionCorrectivaRepository
from src.domain.repositories.i_alerta_repository import IAlertaRepository


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
    ahora aplica la máquina de estados completa, no solo el booleano
    `revisada` — `AlertaTermica.reconocer()` rechaza reconocer dos veces."""

    def __init__(self, alerta_repository: IAlertaRepository) -> None:
        self._alerta_repository = alerta_repository

    async def execute(self, alerta_id: UUID, usuario_id: UUID) -> AlertaTermica:
        alerta = await self._alerta_repository.obtener_por_id(alerta_id)
        if alerta is None:
            raise RecursoNoEncontradoError(f"Alerta {alerta_id} no encontrada")
        alerta.reconocer(usuario_id, datetime.now(tz=timezone.utc))
        return await self._alerta_repository.actualizar(alerta)


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
