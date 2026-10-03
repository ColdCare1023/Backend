from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.repositories.i_reporte_repository import IReporteRepository
from src.infrastructure.database.models import ReportExportModel


class SQLAlchemyReporteRepository(IReporteRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def registrar_exportacion(
        self,
        usuario_id: UUID,
        tipo_reporte: str,
        fecha_desde: datetime,
        fecha_hasta: datetime,
        archivo_url: str | None = None,
    ) -> dict:
        model = ReportExportModel(
            usuario_id=usuario_id,
            tipo_reporte=tipo_reporte,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            archivo_url=archivo_url,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return {
            "id": model.id,
            "usuario_id": model.usuario_id,
            "tipo_reporte": model.tipo_reporte,
            "fecha_desde": model.fecha_desde,
            "fecha_hasta": model.fecha_hasta,
            "archivo_url": model.archivo_url,
            "created_at": model.created_at,
        }
