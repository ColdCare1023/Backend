import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.repositories.i_firmware_repository import IFirmwareRepository
from src.infrastructure.database.models import FirmwareDeploymentModel, FirmwareReleaseModel


def _release_to_dict(model: FirmwareReleaseModel) -> dict:
    return {
        "id": model.id,
        "version": model.version,
        "hash_sha256": model.hash_sha256,
        "descripcion": model.descripcion,
        "fecha_compilacion": model.fecha_compilacion,
        "created_at": model.created_at,
    }


def _despliegue_to_dict(model: FirmwareDeploymentModel) -> dict:
    return {
        "id": model.id,
        "device_id": model.device_id,
        "version_objetivo": model.version_objetivo,
        "estado": model.estado,
        "programado_para": model.programado_para,
        "resultado": model.resultado,
        "completado_en": model.completado_en,
        "created_at": model.created_at,
    }


class SQLAlchemyFirmwareRepository(IFirmwareRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def crear_release(
        self, version: str, hash_sha256: str, descripcion: str, fecha_compilacion: datetime
    ) -> dict:
        model = FirmwareReleaseModel(
            id=uuid.uuid4(),
            version=version,
            hash_sha256=hash_sha256,
            descripcion=descripcion,
            fecha_compilacion=fecha_compilacion,
        )
        self._session.add(model)
        await self._session.flush()
        return _release_to_dict(model)

    async def obtener_release(self, version: str) -> dict | None:
        stmt = select(FirmwareReleaseModel).where(FirmwareReleaseModel.version == version)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _release_to_dict(model) if model else None

    async def listar_releases(self) -> list[dict]:
        result = await self._session.execute(select(FirmwareReleaseModel))
        return [_release_to_dict(m) for m in result.scalars().all()]

    async def crear_despliegue(
        self, device_id: str, version_objetivo: str, programado_para: datetime | None
    ) -> dict:
        model = FirmwareDeploymentModel(
            id=uuid.uuid4(),
            device_id=device_id,
            version_objetivo=version_objetivo,
            estado="programado",
            programado_para=programado_para,
        )
        self._session.add(model)
        await self._session.flush()
        return _despliegue_to_dict(model)

    async def obtener_despliegue(self, despliegue_id: uuid.UUID) -> dict | None:
        model = await self._session.get(FirmwareDeploymentModel, despliegue_id)
        return _despliegue_to_dict(model) if model else None

    async def actualizar_despliegue(
        self, despliegue_id: uuid.UUID, estado: str, resultado: str | None, completado_en: datetime | None
    ) -> dict:
        model = await self._session.get(FirmwareDeploymentModel, despliegue_id)
        if model is None:
            raise ValueError(f"Despliegue {despliegue_id} no encontrado")
        model.estado = estado
        model.resultado = resultado
        model.completado_en = completado_en
        await self._session.flush()
        return _despliegue_to_dict(model)
