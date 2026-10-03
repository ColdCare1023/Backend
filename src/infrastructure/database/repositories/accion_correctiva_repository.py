from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.accion_correctiva import AccionCorrectiva
from src.domain.repositories.i_accion_correctiva_repository import IAccionCorrectivaRepository
from src.infrastructure.database.models import CorrectiveActionModel


def _to_entity(model: CorrectiveActionModel) -> AccionCorrectiva:
    return AccionCorrectiva(
        id=model.id,
        alert_id=model.alert_id,
        usuario_id=model.usuario_id,
        descripcion=model.descripcion,
        created_at=model.created_at,
        corrige_accion_id=model.corrige_accion_id,
    )


class SQLAlchemyAccionCorrectivaRepository(IAccionCorrectivaRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def agregar(self, accion: AccionCorrectiva) -> AccionCorrectiva:
        model = CorrectiveActionModel(
            alert_id=accion.alert_id,
            usuario_id=accion.usuario_id,
            descripcion=accion.descripcion,
            corrige_accion_id=accion.corrige_accion_id,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_entity(model)

    async def listar_por_alerta(self, alert_id: UUID) -> list[AccionCorrectiva]:
        # HU-23 criterio 4: orden cronológico para reconstruir el ciclo de
        # atención de la alerta (acción original + rectificaciones, HU-28).
        stmt = (
            select(CorrectiveActionModel)
            .where(CorrectiveActionModel.alert_id == alert_id)
            .order_by(CorrectiveActionModel.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return [_to_entity(m) for m in result.scalars().all()]

    async def obtener_por_id(self, accion_id: UUID) -> AccionCorrectiva | None:
        model = await self._session.get(CorrectiveActionModel, accion_id)
        return _to_entity(model) if model else None
