from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.repositories.i_audit_log_repository import IAuditLogRepository
from src.domain.value_objects.resultado_auditoria import (
    ACCIONES_NO_EXITOSAS,
    ACCIONES_POR_RESULTADO,
    ResultadoAuditoria,
    resultado_de_accion,
)
from src.infrastructure.database.models import AuditLogModel


def _condicion_resultado(resultado: ResultadoAuditoria):
    """Filas con ese resultado guardado, o anteriores a la migración 0039
    (resultado NULL) cuya acción deriva en él: el mismo criterio que
    `resultado_de_accion`, para que filtro y respuesta coincidan."""
    if resultado == ResultadoAuditoria.EXITOSO:
        legado = AuditLogModel.accion.not_in(sorted(ACCIONES_NO_EXITOSAS))
    else:
        legado = AuditLogModel.accion.in_(sorted(ACCIONES_POR_RESULTADO[resultado]))
    return or_(AuditLogModel.resultado == resultado.value, and_(AuditLogModel.resultado.is_(None), legado))


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
        resultado: str | None = None,
    ) -> None:
        model = AuditLogModel(
            usuario_id=usuario_id,
            accion=accion,
            # audit_logs.recurso es VARCHAR(100): PostgreSQL rechaza lo que exceda.
            recurso=recurso[:100],
            detalle=detalle,
            ip_origen=ip_origen,
            resultado=ResultadoAuditoria(resultado or resultado_de_accion(accion)).value,
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
        resultado: str | None = None,
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
        if resultado is not None:
            stmt = stmt.where(_condicion_resultado(ResultadoAuditoria(resultado)))
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
                "resultado": m.resultado or resultado_de_accion(m.accion).value,
            }
            for m in result.scalars().all()
        ]
