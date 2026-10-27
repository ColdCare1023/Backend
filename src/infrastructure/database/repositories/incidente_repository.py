from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.repositories.i_incidente_repository import (
    TIPO_SIN_DATOS,
    IIncidenteRepository,
    IncidenteOperativo,
)
from src.infrastructure.database.models import IncidenteOperativoModel


def _to_entity(model: IncidenteOperativoModel) -> IncidenteOperativo:
    return IncidenteOperativo(
        id=model.id,
        device_id=model.device_id,
        tipo=model.tipo,
        abierto_en=model.abierto_en,
        ultimo_reporte_en=model.ultimo_reporte_en,
        umbral_segundos=model.umbral_segundos,
        resuelto_en=model.resuelto_en,
        notificado_en=model.notificado_en,
        notificacion_intentos=model.notificacion_intentos or 0,
        detalle=model.detalle,
    )


class SQLAlchemyIncidenteRepository(IIncidenteRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _modelo(self, incidente_id: UUID) -> IncidenteOperativoModel:
        model = await self._session.get(IncidenteOperativoModel, incidente_id)
        if model is None:
            raise ValueError(f"Incidente {incidente_id} no encontrado")
        return model

    async def abierto(self, device_id: str, tipo: str = TIPO_SIN_DATOS) -> IncidenteOperativo | None:
        stmt = select(IncidenteOperativoModel).where(
            IncidenteOperativoModel.device_id == device_id,
            IncidenteOperativoModel.tipo == tipo,
            IncidenteOperativoModel.resuelto_en.is_(None),
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_entity(model) if model else None

    async def abrir(
        self, device_id: str, abierto_en: datetime, ultimo_reporte_en: datetime | None, umbral_segundos: int,
        tipo: str = TIPO_SIN_DATOS, detalle: dict | None = None,
    ) -> IncidenteOperativo:
        model = IncidenteOperativoModel(
            device_id=device_id, tipo=tipo, abierto_en=abierto_en,
            ultimo_reporte_en=ultimo_reporte_en, umbral_segundos=umbral_segundos, detalle=detalle,
        )
        self._session.add(model)
        await self._session.flush()
        return _to_entity(model)

    async def resolver(self, incidente_id: UUID, cuando: datetime) -> IncidenteOperativo:
        model = await self._modelo(incidente_id)
        model.resuelto_en = cuando
        await self._session.flush()
        return _to_entity(model)

    async def actualizar_detalle(self, incidente_id: UUID, detalle: dict) -> None:
        model = await self._modelo(incidente_id)
        model.detalle = detalle
        await self._session.flush()

    async def obtener(self, incidente_id: UUID) -> IncidenteOperativo | None:
        model = await self._session.get(IncidenteOperativoModel, incidente_id)
        return _to_entity(model) if model else None

    async def listar(self, device_id: str | None = None, limite: int = 100) -> list[IncidenteOperativo]:
        stmt = select(IncidenteOperativoModel).order_by(IncidenteOperativoModel.abierto_en.desc()).limit(limite)
        if device_id is not None:
            stmt = stmt.where(IncidenteOperativoModel.device_id == device_id)
        return [_to_entity(m) for m in (await self._session.execute(stmt)).scalars().all()]

    async def listar_pendientes_de_notificar(self, desde: datetime, max_intentos: int) -> list[IncidenteOperativo]:
        stmt = select(IncidenteOperativoModel).where(
            IncidenteOperativoModel.resuelto_en.is_(None),
            IncidenteOperativoModel.notificado_en.is_(None),
            IncidenteOperativoModel.notificacion_intentos < max_intentos,
            IncidenteOperativoModel.abierto_en >= desde,
        )
        return [_to_entity(m) for m in (await self._session.execute(stmt)).scalars().all()]

    async def marcar_notificado(self, incidente_id: UUID, cuando: datetime) -> None:
        model = await self._modelo(incidente_id)
        model.notificado_en = cuando
        model.notificacion_intentos = (model.notificacion_intentos or 0) + 1
        await self._session.flush()

    async def registrar_intento_fallido(self, incidente_id: UUID) -> int:
        model = await self._modelo(incidente_id)
        model.notificacion_intentos = (model.notificacion_intentos or 0) + 1
        await self._session.flush()
        return model.notificacion_intentos
