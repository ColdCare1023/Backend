from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.version_modelo_ia import VersionModeloIA
from src.domain.repositories.i_modelo_ia_repository import IModeloIARepository
from src.infrastructure.database.models import ModeloIAVersionModel


def _to_entity(model: ModeloIAVersionModel) -> VersionModeloIA:
    return VersionModeloIA(
        id=model.id,
        version=model.version,
        model_hash=model.model_hash,
        feature_schema_version=model.feature_schema_version,
        dataset_version=model.dataset_version,
        scikit_learn_version=model.scikit_learn_version,
        python_version=model.python_version,
        trained_at=model.trained_at,
        aprobado_por=model.aprobado_por,
        aprobado_en=model.aprobado_en,
        activa=model.activa,
        activada_en=model.activada_en,
        desactivada_en=model.desactivada_en,
    )


class SQLAlchemyModeloIARepository(IModeloIARepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def registrar(self, version: VersionModeloIA) -> VersionModeloIA:
        model = ModeloIAVersionModel(
            version=version.version,
            model_hash=version.model_hash,
            feature_schema_version=version.feature_schema_version,
            dataset_version=version.dataset_version,
            scikit_learn_version=version.scikit_learn_version,
            python_version=version.python_version,
            trained_at=version.trained_at,
            aprobado_por=version.aprobado_por,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_entity(model)

    async def obtener_por_version(self, version: str) -> VersionModeloIA | None:
        stmt = select(ModeloIAVersionModel).where(ModeloIAVersionModel.version == version)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_entity(model) if model else None

    async def obtener_activa(self) -> VersionModeloIA | None:
        stmt = select(ModeloIAVersionModel).where(ModeloIAVersionModel.activa.is_(True))
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_entity(model) if model else None

    async def activar(self, version: str, cuando: datetime) -> VersionModeloIA:
        anterior = await self.obtener_activa()
        if anterior is not None and anterior.version != version:
            model_anterior = await self._session.get(ModeloIAVersionModel, anterior.id)
            model_anterior.activa = False
            model_anterior.desactivada_en = cuando

        stmt = select(ModeloIAVersionModel).where(ModeloIAVersionModel.version == version)
        result = await self._session.execute(stmt)
        model = result.scalar_one()
        model.activa = True
        model.activada_en = cuando
        await self._session.flush()
        await self._session.refresh(model)
        return _to_entity(model)

    async def listar(self) -> list[VersionModeloIA]:
        stmt = select(ModeloIAVersionModel).order_by(ModeloIAVersionModel.aprobado_en.desc())
        result = await self._session.execute(stmt)
        return [_to_entity(m) for m in result.scalars().all()]

    async def obtener_version_activa_en(self, momento: datetime) -> VersionModeloIA | None:
        stmt = select(ModeloIAVersionModel).where(
            ModeloIAVersionModel.activada_en.is_not(None),
            ModeloIAVersionModel.activada_en <= momento,
            (ModeloIAVersionModel.desactivada_en.is_(None))
            | (ModeloIAVersionModel.desactivada_en > momento),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_entity(model) if model else None
