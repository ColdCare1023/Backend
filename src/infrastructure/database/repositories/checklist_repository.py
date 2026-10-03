from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.checklist_bpa import ITEMS_CHECKLIST_BPA, ChecklistBPA
from src.domain.repositories.i_checklist_repository import IChecklistRepository
from src.infrastructure.database.models import ChecklistBPAModel


def _to_entity(model: ChecklistBPAModel) -> ChecklistBPA:
    return ChecklistBPA(
        id=model.id,
        usuario_id=model.usuario_id,
        fecha=model.fecha,
        observaciones=model.observaciones,
        created_at=model.created_at,
        updated_at=model.updated_at,
        **{clave: getattr(model, clave) for clave in ITEMS_CHECKLIST_BPA},
    )


class SQLAlchemyChecklistRepository(IChecklistRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def guardar(self, checklist: ChecklistBPA) -> ChecklistBPA:
        """Upsert por (usuario_id, fecha). No usa INSERT ... ON CONFLICT para
        no atarse a un dialecto: el backend corre sobre PostgreSQL en
        despliegue y SQLite en la suite de pruebas."""
        resultado = await self._session.execute(
            select(ChecklistBPAModel).where(
                ChecklistBPAModel.usuario_id == checklist.usuario_id,
                ChecklistBPAModel.fecha == checklist.fecha,
            )
        )
        model = resultado.scalar_one_or_none()

        if model is None:
            model = ChecklistBPAModel(
                usuario_id=checklist.usuario_id,
                fecha=checklist.fecha,
                observaciones=checklist.observaciones,
                **checklist.items(),
            )
            self._session.add(model)
        else:
            for clave, valor in checklist.items().items():
                setattr(model, clave, valor)
            model.observaciones = checklist.observaciones
            model.updated_at = datetime.now(tz=timezone.utc)

        await self._session.flush()
        await self._session.refresh(model)
        return _to_entity(model)

    async def obtener_por_usuario_y_fecha(self, usuario_id: UUID, fecha: str) -> ChecklistBPA | None:
        resultado = await self._session.execute(
            select(ChecklistBPAModel).where(
                ChecklistBPAModel.usuario_id == usuario_id,
                ChecklistBPAModel.fecha == fecha,
            )
        )
        model = resultado.scalar_one_or_none()
        return _to_entity(model) if model else None

    async def obtener_ultimo_por_usuario(self, usuario_id: UUID) -> ChecklistBPA | None:
        resultado = await self._session.execute(
            select(ChecklistBPAModel)
            .where(ChecklistBPAModel.usuario_id == usuario_id)
            .order_by(ChecklistBPAModel.fecha.desc(), ChecklistBPAModel.created_at.desc())
            .limit(1)
        )
        model = resultado.scalar_one_or_none()
        return _to_entity(model) if model else None

    async def listar_por_usuario(
        self, usuario_id: UUID, limite: int = 50, offset: int = 0
    ) -> list[ChecklistBPA]:
        resultado = await self._session.execute(
            select(ChecklistBPAModel)
            .where(ChecklistBPAModel.usuario_id == usuario_id)
            .order_by(ChecklistBPAModel.fecha.desc())
            .limit(limite)
            .offset(offset)
        )
        return [_to_entity(m) for m in resultado.scalars().all()]

    async def listar_por_rango_fechas(self, desde: str, hasta: str) -> list[ChecklistBPA]:
        resultado = await self._session.execute(
            select(ChecklistBPAModel)
            .where(ChecklistBPAModel.fecha >= desde, ChecklistBPAModel.fecha <= hasta)
            .order_by(ChecklistBPAModel.fecha.asc())
        )
        return [_to_entity(m) for m in resultado.scalars().all()]
