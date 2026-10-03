from abc import ABC, abstractmethod
from uuid import UUID

from src.domain.entities.accion_correctiva import AccionCorrectiva


class IAccionCorrectivaRepository(ABC):
    @abstractmethod
    async def agregar(self, accion: AccionCorrectiva) -> AccionCorrectiva: ...

    @abstractmethod
    async def listar_por_alerta(self, alert_id) -> list[AccionCorrectiva]: ...

    @abstractmethod
    async def obtener_por_id(self, accion_id: UUID) -> AccionCorrectiva | None: ...
