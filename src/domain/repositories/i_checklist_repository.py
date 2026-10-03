from abc import ABC, abstractmethod
from uuid import UUID

from src.domain.entities.checklist_bpa import ChecklistBPA


class IChecklistRepository(ABC):
    @abstractmethod
    async def guardar(self, checklist: ChecklistBPA) -> ChecklistBPA:
        """Upsert por (usuario_id, fecha): un checklist por usuario y día."""

    @abstractmethod
    async def obtener_por_usuario_y_fecha(self, usuario_id: UUID, fecha: str) -> ChecklistBPA | None: ...

    @abstractmethod
    async def obtener_ultimo_por_usuario(self, usuario_id: UUID) -> ChecklistBPA | None: ...

    @abstractmethod
    async def listar_por_usuario(
        self, usuario_id: UUID, limite: int = 50, offset: int = 0
    ) -> list[ChecklistBPA]: ...

    @abstractmethod
    async def listar_por_rango_fechas(self, desde: str, hasta: str) -> list[ChecklistBPA]: ...
