"""BE-009: JWT invalidados (logout, rechazo de privacidad) y tickets SSE usados."""

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models import InvalidatedJtiModel

MOTIVO_LOGOUT = "logout"
MOTIVO_PRIVACIDAD_RECHAZADA = "privacidad_rechazada"
MOTIVO_TICKET_SSE = "ticket_sse"


class SQLAlchemyJtiRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def invalidar(self, jti: str, expira_en: datetime, motivo: str) -> bool:
        """Registra el jti. True si es la primera vez; False si ya estaba
        (para un ticket SSE: ya se había usado). Atómico entre instancias: lo
        decide la clave primaria, no una lectura previa."""
        await self._purgar_expirados()
        try:
            async with self._session.begin_nested():
                self._session.add(InvalidatedJtiModel(jti=jti, motivo=motivo, expira_en=expira_en))
        except IntegrityError:
            return False
        return True

    async def esta_invalidado(self, jti: str) -> bool:
        resultado = await self._session.execute(
            select(InvalidatedJtiModel.jti).where(InvalidatedJtiModel.jti == jti)
        )
        return resultado.scalar_one_or_none() is not None

    async def _purgar_expirados(self) -> None:
        # Un jti expirado ya no autentica por sí solo (exp), así que su fila
        # sobra. Se purga aquí para que la tabla no crezca sin límite.
        await self._session.execute(
            delete(InvalidatedJtiModel).where(InvalidatedJtiModel.expira_en < datetime.now(tz=timezone.utc))
        )
