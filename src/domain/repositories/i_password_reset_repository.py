from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID


class IPasswordResetRepository(ABC):
    """BE-025: enlaces de recuperación de contraseña de un solo uso."""

    @abstractmethod
    async def emitir(self, usuario_id: UUID, token: str, expira_en: datetime) -> None: ...

    @abstractmethod
    async def consumir(self, token: str) -> UUID | None: ...

    @abstractmethod
    async def anular_pendientes(self, usuario_id: UUID) -> None: ...
