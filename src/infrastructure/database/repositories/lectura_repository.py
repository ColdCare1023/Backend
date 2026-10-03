from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.repositories.i_lectura_repository import ILecturaRepository
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.database.models import DeviceModel, ThermalReadingModel


def _to_entity(model: ThermalReadingModel) -> LecturaTermica:
    return LecturaTermica(
        id=model.id,
        device_id=model.device_id,
        timestamp=model.timestamp,
        temperatura_ambiental=model.temperatura_ambiental,
        humedad_ambiental=model.humedad_ambiental,
        temperatura_interna=model.temperatura_interna,
        apertura_refrigerador=model.apertura_refrigerador,
        estado_conectividad=model.estado_conectividad or "offline",
        nivel_riesgo=NivelRiesgo(model.nivel_riesgo) if model.nivel_riesgo else None,
        payload=model.payload,
        modelo_version=model.modelo_version,
        confianza_ia=model.confianza_ia,
        origen_clasificacion=model.origen_clasificacion,
        estado_inferencia=model.estado_inferencia,
        motivo_no_inferencia=model.motivo_no_inferencia,
        reading_id=model.reading_id,
        schema_version=model.schema_version,
        excursion_confirmada=model.excursion_confirmada,
        riesgo_efectivo=NivelRiesgo(model.riesgo_efectivo) if model.riesgo_efectivo else None,
        probabilidades_ia=model.probabilidades_ia,
        vector_features_ia=model.vector_features_ia,
        boot_id=model.boot_id,
        seq_no=model.seq_no,
        time_quality=model.time_quality,
        received_at=model.received_at,
    )


class SQLAlchemyLecturaRepository(ILecturaRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _asegurar_device(self, device_id: str) -> None:
        existente = await self._session.get(DeviceModel, device_id)
        if existente is None:
            self._session.add(DeviceModel(id=device_id))
            await self._session.flush()

    async def agregar(self, lectura: LecturaTermica) -> LecturaTermica:
        await self._asegurar_device(lectura.device_id)
        model = ThermalReadingModel(
            device_id=lectura.device_id,
            timestamp=lectura.timestamp,
            temperatura_ambiental=lectura.temperatura_ambiental,
            humedad_ambiental=lectura.humedad_ambiental,
            temperatura_interna=lectura.temperatura_interna,
            apertura_refrigerador=lectura.apertura_refrigerador,
            nivel_riesgo=lectura.nivel_riesgo.value if lectura.nivel_riesgo else None,
            estado_conectividad=lectura.estado_conectividad,
            payload=lectura.payload,
            modelo_version=lectura.modelo_version,
            confianza_ia=lectura.confianza_ia,
            origen_clasificacion=lectura.origen_clasificacion,
            estado_inferencia=lectura.estado_inferencia,
            motivo_no_inferencia=lectura.motivo_no_inferencia,
            reading_id=lectura.reading_id,
            schema_version=lectura.schema_version,
            excursion_confirmada=lectura.excursion_confirmada,
            riesgo_efectivo=lectura.riesgo_efectivo.value if lectura.riesgo_efectivo else None,
            probabilidades_ia=lectura.probabilidades_ia,
            vector_features_ia=lectura.vector_features_ia,
            boot_id=lectura.boot_id,
            seq_no=lectura.seq_no,
            time_quality=lectura.time_quality,
            received_at=lectura.received_at,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_entity(model)

    async def obtener_por_id(self, lectura_id: UUID) -> LecturaTermica | None:
        model = await self._session.get(ThermalReadingModel, lectura_id)
        return _to_entity(model) if model else None

    async def listar(
        self,
        device_id: str | None = None,
        nivel_riesgo: str | None = None,
        estado_conectividad: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        limite: int = 100,
        offset: int = 0,
    ) -> list[LecturaTermica]:
        stmt = select(ThermalReadingModel)
        if device_id:
            stmt = stmt.where(ThermalReadingModel.device_id == device_id)
        if nivel_riesgo:
            stmt = stmt.where(ThermalReadingModel.nivel_riesgo == nivel_riesgo)
        if estado_conectividad:
            stmt = stmt.where(ThermalReadingModel.estado_conectividad == estado_conectividad)
        if desde:
            stmt = stmt.where(ThermalReadingModel.timestamp >= desde)
        if hasta:
            stmt = stmt.where(ThermalReadingModel.timestamp <= hasta)
        stmt = stmt.order_by(ThermalReadingModel.timestamp.desc()).limit(limite).offset(offset)
        result = await self._session.execute(stmt)
        return [_to_entity(m) for m in result.scalars().all()]

    async def listar_recientes_por_device(self, device_id: str, limite: int) -> list[LecturaTermica]:
        stmt = (
            select(ThermalReadingModel)
            .where(ThermalReadingModel.device_id == device_id)
            .order_by(ThermalReadingModel.timestamp.desc())
            .limit(limite)
        )
        result = await self._session.execute(stmt)
        return [_to_entity(m) for m in result.scalars().all()]

    async def obtener_por_device_y_timestamp(
        self, device_id: str, timestamp: datetime
    ) -> LecturaTermica | None:
        stmt = select(ThermalReadingModel).where(
            ThermalReadingModel.device_id == device_id,
            ThermalReadingModel.timestamp == timestamp,
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_entity(model) if model else None

    async def obtener_por_device_boot_seq(
        self, device_id: str, boot_id: int, seq_no: int
    ) -> LecturaTermica | None:
        stmt = select(ThermalReadingModel).where(
            ThermalReadingModel.device_id == device_id,
            ThermalReadingModel.boot_id == boot_id,
            ThermalReadingModel.seq_no == seq_no,
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_entity(model) if model else None
