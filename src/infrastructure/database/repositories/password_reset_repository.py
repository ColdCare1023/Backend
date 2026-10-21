"""BE-025: tokens de recuperación de contraseña (un solo uso, vida corta)."""

import hashlib
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.repositories.i_password_reset_repository import IPasswordResetRepository
from src.infrastructure.database.models import PasswordResetTokenModel


def huella_token(token: str) -> str:
    """SHA-256 del token: la base nunca guarda un valor utilizable."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SQLAlchemyPasswordResetRepository(IPasswordResetRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def emitir(self, usuario_id: UUID, token: str, expira_en: datetime) -> None:
        """Registra un token nuevo y anula los pendientes del usuario: solo el
        último enlace enviado vale."""
        await self.anular_pendientes(usuario_id)
        self._session.add(
            PasswordResetTokenModel(usuario_id=usuario_id, token_hash=huella_token(token), expira_en=expira_en)
        )
        await self._session.flush()

    async def consumir(self, token: str) -> UUID | None:
        """Marca el token como usado y devuelve su usuario, solo si existía,
        no estaba usado y no había caducado. Atómico: dos peticiones con el
        mismo token no pueden consumirlo ambas (UPDATE condicional)."""
        ahora = datetime.now(tz=timezone.utc)
        resultado = await self._session.execute(
            update(PasswordResetTokenModel)
            .where(
                PasswordResetTokenModel.token_hash == huella_token(token),
                PasswordResetTokenModel.usado_en.is_(None),
                PasswordResetTokenModel.expira_en > ahora,
            )
            .values(usado_en=ahora)
            .returning(PasswordResetTokenModel.usuario_id)
        )
        return resultado.scalar_one_or_none()

    async def anular_pendientes(self, usuario_id: UUID) -> None:
        """Tras cualquier cambio de contraseña, ningún enlace previo sirve."""
        await self._session.execute(
            delete(PasswordResetTokenModel).where(
                PasswordResetTokenModel.usuario_id == usuario_id, PasswordResetTokenModel.usado_en.is_(None)
            )
        )
