import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.repositories.i_corrupcion_repository import ICorrupcionRepository
from src.infrastructure.database.models import ForensicSnapshotModel, SystemStateModel

_ID_FILA_UNICA = 1


class SQLAlchemyCorrupcionRepository(ICorrupcionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _obtener_o_crear_estado(self) -> SystemStateModel:
        model = await self._session.get(SystemStateModel, _ID_FILA_UNICA)
        if model is None:
            # Backfill defensivo: en Postgres la migración 0005 ya inserta la
            # fila; en SQLite (tests, create_all sin migraciones) no existe.
            model = SystemStateModel(id=_ID_FILA_UNICA, cadena_comprometida=False)
            self._session.add(model)
            await self._session.flush()
        return model

    async def cadena_comprometida(self) -> bool:
        model = await self._obtener_o_crear_estado()
        return model.cadena_comprometida

    async def marcar_comprometida(self) -> None:
        model = await self._obtener_o_crear_estado()
        model.cadena_comprometida = True
        model.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def marcar_restaurada(self) -> None:
        model = await self._obtener_o_crear_estado()
        model.cadena_comprometida = False
        model.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def guardar_snapshot_forense(self, registro_id: uuid.UUID | None, detalle: dict) -> None:
        self._session.add(ForensicSnapshotModel(id=uuid.uuid4(), registro_id=registro_id, detalle=detalle))
        await self._session.flush()
