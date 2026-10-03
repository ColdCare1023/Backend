from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.repositories.i_audit_log_repository import IAuditLogRepository
from src.infrastructure.database.models import AuditLogModel


class SQLAlchemyAuditLogRepository(IAuditLogRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def registrar(
        self,
        usuario_id: UUID | None,
        accion: str,
        recurso: str,
        detalle: dict,
        ip_origen: str | None = None,
    ) -> None:
        model = AuditLogModel(
            usuario_id=usuario_id,
            accion=accion,
            recurso=recurso,
            detalle=detalle,
            ip_origen=ip_origen,
        )
        self._session.add(model)
        await self._session.flush()

    async def listar(
        self,
        limite: int = 100,
        offset: int = 0,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        usuario_id: UUID | None = None,
        accion: str | None = None,
    ) -> list[dict]:
        stmt = select(AuditLogModel)
        if desde is not None:
            stmt = stmt.where(AuditLogModel.created_at >= desde)
        if hasta is not None:
            stmt = stmt.where(AuditLogModel.created_at <= hasta)
        if usuario_id is not None:
            stmt = stmt.where(AuditLogModel.usuario_id == usuario_id)
        if accion is not None:
            stmt = stmt.where(AuditLogModel.accion == accion)
        stmt = stmt.order_by(AuditLogModel.created_at.desc()).limit(limite).offset(offset)
        result = await self._session.execute(stmt)
        return [
            {
                "id": m.id,
                "usuario_id": m.usuario_id,
                "accion": m.accion,
                "recurso": m.recurso,
                "detalle": m.detalle,
                "ip_origen": m.ip_origen,
                "created_at": m.created_at,
            }
            for m in result.scalars().all()
        ]
