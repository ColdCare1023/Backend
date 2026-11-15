from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.repositories.i_estado_nodo_repository import (
    EpisodioBufferSaturado,
    EstadoNodo,
    IEstadoNodoRepository,
)
from src.infrastructure.database.models import EpisodioBufferSaturadoModel, EstadoNodoModel

_CAMPOS_ESTADO = (
    "recibido_en", "ocurrido_en", "boot_id", "pendientes", "capacidad", "ocupacion_pct", "perdidas_total",
    "cuarentena", "pendientes_desde", "pendientes_hasta", "ultima_sincronizacion_en", "detalle",
)
_CAMPOS_EPISODIO = (
    "desde", "hasta", "desde_boot_id", "desde_seq_no", "hasta_boot_id", "hasta_seq_no", "causa", "perdidas",
    "perdidas_total", "abierto", "registrado_en", "actualizado_en", "cerrado_en",
)


def _estado(model: EstadoNodoModel) -> EstadoNodo:
    return EstadoNodo(device_id=model.device_id, **{c: getattr(model, c) for c in _CAMPOS_ESTADO})


def _episodio(model: EpisodioBufferSaturadoModel) -> EpisodioBufferSaturado:
    return EpisodioBufferSaturado(
        id=model.id, device_id=model.device_id, **{c: getattr(model, c) for c in _CAMPOS_EPISODIO}
    )


class SQLAlchemyEstadoNodoRepository(IEstadoNodoRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def obtener_estado(self, device_id: str) -> EstadoNodo | None:
        model = await self._session.get(EstadoNodoModel, device_id)
        return _estado(model) if model else None

    async def guardar_estado(self, estado: EstadoNodo) -> EstadoNodo:
        model = await self._session.get(EstadoNodoModel, estado.device_id)
        if model is None:
            model = EstadoNodoModel(device_id=estado.device_id)
            self._session.add(model)
        for campo in _CAMPOS_ESTADO:
            setattr(model, campo, getattr(estado, campo))
        await self._session.flush()
        return _estado(model)

    async def episodio_por_inicio(
        self, device_id: str, desde_boot_id: int, desde_seq_no: int
    ) -> EpisodioBufferSaturado | None:
        stmt = select(EpisodioBufferSaturadoModel).where(
            EpisodioBufferSaturadoModel.device_id == device_id,
            EpisodioBufferSaturadoModel.desde_boot_id == desde_boot_id,
            EpisodioBufferSaturadoModel.desde_seq_no == desde_seq_no,
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return _episodio(model) if model else None

    async def episodios_abiertos(self, device_id: str) -> list[EpisodioBufferSaturado]:
        stmt = (
            select(EpisodioBufferSaturadoModel)
            .where(EpisodioBufferSaturadoModel.device_id == device_id, EpisodioBufferSaturadoModel.abierto.is_(True))
            .order_by(EpisodioBufferSaturadoModel.registrado_en.desc())
        )
        return [_episodio(m) for m in (await self._session.execute(stmt)).scalars().all()]

    async def agregar_episodio(self, episodio: EpisodioBufferSaturado) -> EpisodioBufferSaturado:
        model = EpisodioBufferSaturadoModel(
            id=episodio.id, device_id=episodio.device_id, **{c: getattr(episodio, c) for c in _CAMPOS_EPISODIO}
        )
        self._session.add(model)
        await self._session.flush()
        return _episodio(model)

    async def actualizar_episodio(self, episodio: EpisodioBufferSaturado) -> EpisodioBufferSaturado:
        model = await self._session.get(EpisodioBufferSaturadoModel, episodio.id)
        if model is None:
            raise ValueError(f"Episodio de buffer {episodio.id} no encontrado")
        for campo in _CAMPOS_EPISODIO:
            setattr(model, campo, getattr(episodio, campo))
        await self._session.flush()
        return _episodio(model)

    async def obtener_episodio(self, episodio_id: UUID) -> EpisodioBufferSaturado | None:
        model = await self._session.get(EpisodioBufferSaturadoModel, episodio_id)
        return _episodio(model) if model else None

    async def listar_episodios(
        self,
        device_id: str,
        limite: int = 10,
        desde: datetime | None = None,
        hasta: datetime | None = None,
    ) -> list[EpisodioBufferSaturado]:
        tabla = EpisodioBufferSaturadoModel
        stmt = select(tabla).where(tabla.device_id == device_id)
        if desde is not None:
            stmt = stmt.where(or_(tabla.hasta.is_(None), tabla.hasta >= desde))
        if hasta is not None:
            stmt = stmt.where(or_(tabla.desde.is_(None), tabla.desde <= hasta))
        if desde is not None and hasta is not None:
            # Un episodio sin ninguna hora no se puede ubicar en el periodo.
            stmt = stmt.where(~and_(tabla.desde.is_(None), tabla.hasta.is_(None)))
        stmt = stmt.order_by(tabla.registrado_en.desc()).limit(limite)
        return [_episodio(m) for m in (await self._session.execute(stmt)).scalars().all()]
