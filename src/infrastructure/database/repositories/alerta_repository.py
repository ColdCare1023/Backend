from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.alerta_termica import AlertaTermica
from src.domain.repositories.i_alerta_repository import IAlertaRepository
from src.domain.value_objects.estado_alerta import EstadoAlerta
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.database.models import ThermalAlertModel


def _to_entity(model: ThermalAlertModel) -> AlertaTermica:
    return AlertaTermica(
        id=model.id,
        reading_id=model.reading_id,
        device_id=model.device_id,
        nivel_riesgo=NivelRiesgo(model.nivel_riesgo),
        mensaje=model.mensaje,
        revisada=model.revisada,
        revisada_por=model.revisada_por,
        created_at=model.created_at,
        episodio_abierto=model.episodio_abierto == 1,
        lectura_inicial_id=model.lectura_inicial_id,
        lectura_mas_reciente_id=model.lectura_mas_reciente_id,
        ultima_actualizacion=model.ultima_actualizacion,
        cerrada_en=model.cerrada_en,
        estado=EstadoAlerta(model.estado),
        reconocida_en=model.reconocida_en,
        atendida_en=model.atendida_en,
    )


class SQLAlchemyAlertaRepository(IAlertaRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def agregar(self, alerta: AlertaTermica) -> AlertaTermica:
        model = ThermalAlertModel(
            reading_id=alerta.reading_id,
            device_id=alerta.device_id,
            nivel_riesgo=alerta.nivel_riesgo.value,
            mensaje=alerta.mensaje,
            revisada=alerta.revisada,
            revisada_por=alerta.revisada_por,
            episodio_abierto=1 if alerta.episodio_abierto else None,
            lectura_inicial_id=alerta.lectura_inicial_id or alerta.reading_id,
            lectura_mas_reciente_id=alerta.lectura_mas_reciente_id or alerta.reading_id,
            ultima_actualizacion=alerta.ultima_actualizacion,
            cerrada_en=alerta.cerrada_en,
            estado=alerta.estado.value,
            reconocida_en=alerta.reconocida_en,
            atendida_en=alerta.atendida_en,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_entity(model)

    async def obtener_por_id(self, alerta_id: UUID) -> AlertaTermica | None:
        model = await self._session.get(ThermalAlertModel, alerta_id)
        return _to_entity(model) if model else None

    async def listar(
        self,
        device_id: str | None = None,
        revisada: bool | None = None,
        estado: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        limite: int = 100,
        offset: int = 0,
    ) -> list[AlertaTermica]:
        stmt = select(ThermalAlertModel)
        if device_id:
            stmt = stmt.where(ThermalAlertModel.device_id == device_id)
        if revisada is not None:
            stmt = stmt.where(ThermalAlertModel.revisada == revisada)
        if estado is not None:
            stmt = stmt.where(ThermalAlertModel.estado == estado)
        # Acotar por periodo es indispensable en el reporte BPA (RF-13): sin
        # esto el reporte de un mes incluía las alertas de todo el histórico.
        if desde is not None:
            stmt = stmt.where(ThermalAlertModel.created_at >= desde)
        if hasta is not None:
            stmt = stmt.where(ThermalAlertModel.created_at <= hasta)
        stmt = stmt.order_by(ThermalAlertModel.created_at.desc()).limit(limite).offset(offset)
        result = await self._session.execute(stmt)
        return [_to_entity(m) for m in result.scalars().all()]

    async def actualizar(self, alerta: AlertaTermica) -> AlertaTermica:
        model = await self._session.get(ThermalAlertModel, alerta.id)
        if model is None:
            raise ValueError(f"Alerta {alerta.id} no encontrada")
        model.revisada = alerta.revisada
        model.revisada_por = alerta.revisada_por
        model.episodio_abierto = 1 if alerta.episodio_abierto else None
        model.lectura_mas_reciente_id = alerta.lectura_mas_reciente_id
        model.ultima_actualizacion = alerta.ultima_actualizacion
        model.cerrada_en = alerta.cerrada_en
        model.estado = alerta.estado.value
        model.reconocida_en = alerta.reconocida_en
        model.atendida_en = alerta.atendida_en
        await self._session.flush()
        await self._session.refresh(model)
        return _to_entity(model)

    async def marcar_atendida_si_no_atendida(self, alerta_id: UUID, timestamp: datetime) -> bool:
        stmt = (
            update(ThermalAlertModel)
            .where(ThermalAlertModel.id == alerta_id, ThermalAlertModel.estado != EstadoAlerta.ATENDIDA.value)
            .values(estado=EstadoAlerta.ATENDIDA.value, atendida_en=timestamp)
        )
        resultado = await self._session.execute(stmt)
        await self._session.flush()
        return resultado.rowcount == 1

    async def obtener_episodio_abierto(self, device_id: str) -> AlertaTermica | None:
        stmt = (
            select(ThermalAlertModel)
            .where(ThermalAlertModel.device_id == device_id, ThermalAlertModel.episodio_abierto == 1)
            .order_by(ThermalAlertModel.ultima_actualizacion.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_entity(model) if model else None

    async def obtener_ultimo_cerrado(
        self, device_id: str, nivel_riesgo: NivelRiesgo
    ) -> AlertaTermica | None:
        stmt = (
            select(ThermalAlertModel)
            .where(
                ThermalAlertModel.device_id == device_id,
                ThermalAlertModel.nivel_riesgo == nivel_riesgo.value,
                ThermalAlertModel.episodio_abierto.is_(None),
                ThermalAlertModel.cerrada_en.is_not(None),
            )
            .order_by(ThermalAlertModel.cerrada_en.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_entity(model) if model else None
