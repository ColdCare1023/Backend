from abc import ABC, abstractmethod
from uuid import UUID

from src.domain.entities.accion_correctiva import AccionCorrectiva


class IAccionCorrectivaRepository(ABC):
    @abstractmethod
    async def agregar(self, accion: AccionCorrectiva) -> AccionCorrectiva: ...

    @abstractmethod
    async def listar_por_alerta(self, alert_id) -> list[AccionCorrectiva]: ...

    async def listar_por_alertas(self, alert_ids: list[UUID]) -> list[AccionCorrectiva]:
        acciones: list[AccionCorrectiva] = []
        for alert_id in alert_ids:
            acciones.extend(await self.listar_por_alerta(alert_id))
        return acciones

    @abstractmethod
    async def obtener_por_id(self, accion_id: UUID) -> AccionCorrectiva | None: ...
