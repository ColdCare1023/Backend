from abc import ABC, abstractmethod
from uuid import UUID

from src.domain.entities.usuario import Usuario


class IUsuarioRepository(ABC):
    @abstractmethod
    async def agregar(self, usuario: Usuario) -> Usuario: ...

    @abstractmethod
    async def obtener_por_email(self, email: str) -> Usuario | None: ...

    @abstractmethod
    async def obtener_por_id(self, usuario_id: UUID) -> Usuario | None: ...

    @abstractmethod
    async def listar(self) -> list[Usuario]: ...

    @abstractmethod
    async def actualizar(self, usuario: Usuario) -> Usuario: ...
