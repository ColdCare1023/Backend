from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID


class IReporteRepository(ABC):
    @abstractmethod
    async def registrar_exportacion(
        self,
        usuario_id: UUID,
        tipo_reporte: str,
        fecha_desde: datetime,
        fecha_hasta: datetime,
        archivo_url: str | None = None,
    ) -> dict: ...
