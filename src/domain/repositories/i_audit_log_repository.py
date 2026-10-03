from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID


class IAuditLogRepository(ABC):
    @abstractmethod
    async def registrar(
        self,
        usuario_id: UUID | None,
        accion: str,
        recurso: str,
        detalle: dict,
        ip_origen: str | None = None,
    ) -> None: ...

    @abstractmethod
    async def listar(
        self,
        limite: int = 100,
        offset: int = 0,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        usuario_id: UUID | None = None,
        accion: str | None = None,
    ) -> list[dict]:
        """HU-42/HU-50: el auditor filtra la bitácora por periodo, actor o
        tipo de acción y obtiene una vista de solo lectura."""
        ...
