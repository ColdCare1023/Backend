from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.registro_trazabilidad import RegistroTrazabilidad
from src.domain.repositories.i_trazabilidad_repository import ITrazabilidadRepository
from src.domain.value_objects.hash_encadenado import GENESIS_HASH, HashEncadenado
from src.infrastructure.database.models import TraceabilityRecordModel


def _to_entity(model: TraceabilityRecordModel) -> RegistroTrazabilidad:
    return RegistroTrazabilidad(
        id=model.id,
        tipo_evento=model.tipo_evento,
        payload=model.payload,
        timestamp=model.timestamp,
        hash_encadenado=HashEncadenado(previous_hash=model.previous_hash, hash_actual=model.hash_actual),
        chain_id=model.chain_id,
        chain_seq=model.chain_seq,
        hash_version=model.hash_version,
        device_id=model.device_id,
        usuario_id=model.usuario_id,
    )


class SQLAlchemyTrazabilidadRepository(ITrazabilidadRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def agregar(self, registro: RegistroTrazabilidad) -> RegistroTrazabilidad:
        model = TraceabilityRecordModel(
            tipo_evento=registro.tipo_evento,
            device_id=registro.device_id,
            usuario_id=registro.usuario_id,
            payload=registro.payload,
            timestamp=registro.timestamp,
            previous_hash=registro.previous_hash,
            hash_actual=registro.hash_actual,
            chain_id=registro.chain_id,
            chain_seq=registro.chain_seq,
            hash_version=registro.hash_version,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_entity(model)

    async def obtener_ultimo_eslabon(self, chain_id: str) -> tuple[str, int]:
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            # Candado por cadena (hashtext(chain_id) -> int, cast implícito a
            # bigint): serializa lectura-luego-escritura SOLO dentro de la
            # misma cadena; cadenas de dispositivos distintos no se bloquean
            # entre sí. Se libera automáticamente al terminar la transacción
            # (variante _xact_). Sin efecto en SQLite (tests con aiosqlite).
            await self._session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:chain_id))"),
                {"chain_id": chain_id},
            )
        # Desempate por `id` además de `chain_seq`/`created_at`: incluso dentro
        # de una misma cadena, dos inserciones podrían compartir created_at por
        # resolución de reloj; el mismo criterio de desempate debe usarse aquí
        # (camino de escritura) y en listar_todos_ordenados (camino de
        # verificación) para que ambos recorran la cadena en el mismo orden.
        stmt = (
            select(TraceabilityRecordModel.hash_actual, TraceabilityRecordModel.chain_seq)
            .where(TraceabilityRecordModel.chain_id == chain_id)
            .order_by(TraceabilityRecordModel.chain_seq.desc(), TraceabilityRecordModel.id.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        fila = result.one_or_none()
        if fila is None:
            return GENESIS_HASH, 0
        hash_actual, chain_seq = fila
        return hash_actual, chain_seq

    async def listar_todos_ordenados(self, chain_id: str | None = None) -> list[RegistroTrazabilidad]:
        stmt = select(TraceabilityRecordModel)
        if chain_id is not None:
            stmt = stmt.where(TraceabilityRecordModel.chain_id == chain_id)
            stmt = stmt.order_by(TraceabilityRecordModel.chain_seq.asc(), TraceabilityRecordModel.id.asc())
        else:
            # Orden global de inserción: mismo criterio de desempate que
            # obtener_ultimo_eslabon(), en sentido inverso.
            stmt = stmt.order_by(TraceabilityRecordModel.created_at.asc(), TraceabilityRecordModel.id.asc())
        result = await self._session.execute(stmt)
        return [_to_entity(m) for m in result.scalars().all()]

    async def listar(
        self,
        tipo_evento: str | None = None,
        device_id: str | None = None,
        chain_id: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        limite: int = 100,
        offset: int = 0,
    ) -> list[RegistroTrazabilidad]:
        stmt = select(TraceabilityRecordModel)
        if tipo_evento:
            stmt = stmt.where(TraceabilityRecordModel.tipo_evento == tipo_evento)
        if device_id:
            stmt = stmt.where(TraceabilityRecordModel.device_id == device_id)
        if chain_id:
            stmt = stmt.where(TraceabilityRecordModel.chain_id == chain_id)
        # Mismo motivo que en alertas: el reporte BPA debe ceñirse al periodo.
        # Se filtra por `timestamp` (el instante del hecho registrado), que es
        # el campo que el propio reporte muestra al auditor.
        if desde is not None:
            stmt = stmt.where(TraceabilityRecordModel.timestamp >= desde)
        if hasta is not None:
            stmt = stmt.where(TraceabilityRecordModel.timestamp <= hasta)
        stmt = stmt.order_by(TraceabilityRecordModel.created_at.desc()).limit(limite).offset(offset)
        result = await self._session.execute(stmt)
        return [_to_entity(m) for m in result.scalars().all()]

    async def marcar_corrupto(self, registro_id: UUID) -> None:
        await self._session.execute(
            update(TraceabilityRecordModel)
            .where(TraceabilityRecordModel.id == registro_id)
            .values(is_corrupted=True)
        )
        await self._session.flush()

    async def marcar_posteriores_como_afectados(self, ids: list[UUID]) -> None:
        if not ids:
            return
        await self._session.execute(
            update(TraceabilityRecordModel)
            .where(TraceabilityRecordModel.id.in_(ids))
            .values(is_after_corruption=True)
        )
        await self._session.flush()
