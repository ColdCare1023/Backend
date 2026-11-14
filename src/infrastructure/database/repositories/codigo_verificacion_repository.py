"""Códigos de verificación de un solo uso (login, recuperación, activación)."""

from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.codigo_verificacion import CodigoVerificacion, PropositoCodigo
from src.domain.repositories.i_codigo_verificacion_repository import ICodigoVerificacionRepository
from src.infrastructure.database.models import CodigoVerificacionModel as M
from src.infrastructure.database.models import UserModel


def _to_entity(model: M) -> CodigoVerificacion:
    return CodigoVerificacion(
        id=model.id,
        usuario_id=model.usuario_id,
        proposito=PropositoCodigo(model.proposito),
        codigo_hash=model.codigo_hash,
        desafio_hash=model.desafio_hash,
        expira_en=model.expira_en,
        intentos=model.intentos,
        usado_en=model.usado_en,
        creado_en=model.creado_en,
        emitido_en=model.emitido_en,
        ip=model.ip,
    )


class SQLAlchemyCodigoVerificacionRepository(ICodigoVerificacionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def emitir(
        self, codigo: CodigoVerificacion, anular: Iterable[PropositoCodigo], purgar_antes_de: datetime
    ) -> CodigoVerificacion:
        ahora = codigo.emitido_en or codigo.creado_en or purgar_antes_de
        # Bloqueo de la fila del usuario: dos solicitudes simultáneas no dejan
        # dos códigos vivos (en READ COMMITTED la segunda espera, y su UPDATE
        # ve y anula el código de la primera). SQLite no tiene FOR UPDATE pero
        # serializa las escrituras.
        await self._session.execute(select(UserModel.id).where(UserModel.id == codigo.usuario_id).with_for_update())
        await self.anular_pendientes(codigo.usuario_id, ahora, anular)
        # Purga de los códigos muertos del usuario que ya salieron de la ventana del tope.
        await self._session.execute(
            delete(M).where(
                M.usuario_id == codigo.usuario_id,
                M.emitido_en < purgar_antes_de,
                or_(M.usado_en.is_not(None), M.expira_en <= ahora),
            )
        )
        marcas = {"creado_en": codigo.creado_en, "emitido_en": codigo.emitido_en or codigo.creado_en}
        model = M(
            usuario_id=codigo.usuario_id,
            proposito=codigo.proposito.value,
            codigo_hash=codigo.codigo_hash,
            desafio_hash=codigo.desafio_hash,
            expira_en=codigo.expira_en,
            intentos=0,
            ip=codigo.ip,
            **{k: v for k, v in marcas.items() if v is not None},
        )
        self._session.add(model)
        await self._session.flush()
        return _to_entity(model)

    async def obtener_por_desafio(self, desafio_hash: str, proposito: PropositoCodigo) -> CodigoVerificacion | None:
        resultado = await self._session.execute(
            select(M).where(M.desafio_hash == desafio_hash, M.proposito == proposito.value)
        )
        model = resultado.scalar_one_or_none()
        return _to_entity(model) if model else None

    async def listar_utilizables(
        self, usuario_id: UUID, propositos: Iterable[PropositoCodigo], ahora: datetime, max_intentos: int
    ) -> list[CodigoVerificacion]:
        resultado = await self._session.execute(
            select(M)
            .where(
                M.usuario_id == usuario_id,
                M.proposito.in_([p.value for p in propositos]),
                M.usado_en.is_(None),
                M.expira_en > ahora,
                M.intentos < max_intentos,
            )
            .order_by(M.creado_en.desc())
        )
        return [_to_entity(m) for m in resultado.scalars().all()]

    async def contar_fallos(self, usuario_id: UUID, propositos: Iterable[PropositoCodigo], desde: datetime) -> int:
        resultado = await self._session.execute(
            select(func.coalesce(func.sum(M.intentos), 0)).where(
                M.usuario_id == usuario_id,
                M.proposito.in_([p.value for p in propositos]),
                M.emitido_en >= desde,
            )
        )
        return int(resultado.scalar_one())

    async def registrar_intento_fallido(self, codigo_id: UUID, max_intentos: int, ahora: datetime) -> int:
        # En el SET, `M.intentos` es el valor previo (PostgreSQL y SQLite).
        resultado = await self._session.execute(
            update(M)
            .where(M.id == codigo_id, M.usado_en.is_(None))
            .values(
                intentos=M.intentos + 1,
                usado_en=case((M.intentos + 1 >= max_intentos, ahora), else_=None),
            )
            .returning(M.intentos)
        )
        return int(resultado.scalar_one_or_none() or 0)

    async def consumir(self, codigo_id: UUID, codigo_hash: str, max_intentos: int, ahora: datetime) -> bool:
        resultado = await self._session.execute(
            update(M)
            .where(
                M.id == codigo_id,
                M.codigo_hash == codigo_hash,
                M.usado_en.is_(None),
                M.expira_en > ahora,
                M.intentos < max_intentos,
            )
            .values(usado_en=ahora)
            .returning(M.id)
        )
        return resultado.scalar_one_or_none() is not None

    async def renovar(
        self, codigo_id: UUID, codigo_hash: str, expira_en: datetime, ahora: datetime, emitido_antes_de: datetime
    ) -> bool:
        resultado = await self._session.execute(
            update(M)
            .where(
                M.id == codigo_id,
                M.usado_en.is_(None),
                M.expira_en > ahora,
                M.creado_en <= emitido_antes_de,
            )
            .values(codigo_hash=codigo_hash, creado_en=ahora, expira_en=expira_en)
            .returning(M.id)
        )
        return resultado.scalar_one_or_none() is not None

    async def anular_pendientes(
        self, usuario_id: UUID, ahora: datetime, propositos: Iterable[PropositoCodigo] | None = None
    ) -> None:
        condiciones = [M.usuario_id == usuario_id, M.usado_en.is_(None)]
        if propositos is not None:
            condiciones.append(M.proposito.in_([p.value for p in propositos]))
        await self._session.execute(update(M).where(*condiciones).values(usado_en=ahora))

    async def reiniciar(self, usuario_id: UUID, propositos: Iterable[PropositoCodigo] | None = None) -> None:
        condiciones = [M.usuario_id == usuario_id]
        if propositos is not None:
            condiciones.append(M.proposito.in_([p.value for p in propositos]))
        await self._session.execute(delete(M).where(*condiciones))
