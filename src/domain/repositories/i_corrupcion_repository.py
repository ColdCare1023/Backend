from abc import ABC, abstractmethod
from uuid import UUID


class ICorrupcionRepository(ABC):
    """HU-47: estado global de la cadena y snapshots forenses ante corrupción."""

    @abstractmethod
    async def cadena_comprometida(self) -> bool: ...

    @abstractmethod
    async def marcar_comprometida(self) -> None: ...

    @abstractmethod
    async def marcar_restaurada(self) -> None: ...

    @abstractmethod
    async def guardar_snapshot_forense(self, registro_id: UUID | None, detalle: dict) -> None: ...
