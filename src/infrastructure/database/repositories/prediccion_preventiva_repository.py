from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.prediccion_preventiva import PrediccionPreventiva
from src.domain.repositories.i_prediccion_preventiva_repository import IPrediccionPreventivaRepository
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.database.models import PrediccionPreventivaModel


def _to_entity(model: PrediccionPreventivaModel) -> PrediccionPreventiva:
    return PrediccionPreventiva(
        id=model.id,
        lectura_id=model.lectura_id,
        device_id=model.device_id,
        timestamp_inferencia=model.timestamp_inferencia,
        modo=model.modo,
        estado=model.estado,
        horizonte_min=model.horizonte_min,
        clase=NivelRiesgo(model.clase) if model.clase else None,
        puntuacion=model.puntuacion,
        umbral=model.umbral,
        puntuacion_calibrada=model.puntuacion_calibrada,
        modelo_version=model.modelo_version,
        modelo_sha256=model.modelo_sha256,
        motivo=model.motivo,
        vector_features=model.vector_features,
        creado_en=model.created_at,
    )


class SQLAlchemyPrediccionPreventivaRepository(IPrediccionPreventivaRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def agregar(self, prediccion: PrediccionPreventiva) -> PrediccionPreventiva:
        if prediccion.lectura_id is None:
            raise ValueError("Una predicción preventiva se persiste siempre asociada a su lectura")
        model = PrediccionPreventivaModel(
            lectura_id=prediccion.lectura_id,
            device_id=prediccion.device_id,
            timestamp_inferencia=prediccion.timestamp_inferencia,
            modo=prediccion.modo,
            estado=prediccion.estado,
            horizonte_min=prediccion.horizonte_min,
            clase=prediccion.clase.value if prediccion.clase is not None else None,
            puntuacion=prediccion.puntuacion,
            umbral=prediccion.umbral,
            puntuacion_calibrada=prediccion.puntuacion_calibrada,
            modelo_version=prediccion.modelo_version,
            modelo_sha256=prediccion.modelo_sha256,
            motivo=prediccion.motivo[:200] if prediccion.motivo else None,
            vector_features=prediccion.vector_features,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_entity(model)

    async def listar(
        self,
        device_id: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        limite: int = 100,
    ) -> list[PrediccionPreventiva]:
        stmt = select(PrediccionPreventivaModel)
        if device_id is not None:
            stmt = stmt.where(PrediccionPreventivaModel.device_id == device_id)
        if desde is not None:
            stmt = stmt.where(PrediccionPreventivaModel.timestamp_inferencia >= desde)
        if hasta is not None:
            stmt = stmt.where(PrediccionPreventivaModel.timestamp_inferencia <= hasta)
        stmt = stmt.order_by(PrediccionPreventivaModel.timestamp_inferencia.desc()).limit(limite)
        resultado = await self._session.execute(stmt)
        return [_to_entity(m) for m in resultado.scalars().all()]
