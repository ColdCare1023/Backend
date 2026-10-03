from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from src.domain.entities.registro_trazabilidad import RegistroTrazabilidad


class ITrazabilidadRepository(ABC):
    @abstractmethod
    async def agregar(self, registro: RegistroTrazabilidad) -> RegistroTrazabilidad: ...

    @abstractmethod
    async def marcar_corrupto(self, registro_id: UUID) -> None: ...

    @abstractmethod
    async def marcar_posteriores_como_afectados(self, ids: list[UUID]) -> None: ...

    @abstractmethod
    async def obtener_ultimo_eslabon(self, chain_id: str) -> tuple[str, int]:
        """Devuelve (hash_actual, chain_seq) del último registro de esa
        cadena, o (GENESIS_HASH, 0) si la cadena aún no tiene registros.

        Para PostgreSQL, serializa la sección crítica lectura-luego-escritura
        con un candado consultivo derivado de chain_id (HU-25 criterio 3):
        cadenas distintas no se bloquean entre sí."""
        ...

    @abstractmethod
    async def listar_todos_ordenados(self, chain_id: str | None = None) -> list[RegistroTrazabilidad]:
        """Sin chain_id: todos los registros, orden global de inserción
        (para una verificación que recorre todas las cadenas a la vez). Con
        chain_id: solo esa cadena, en orden de chain_seq (HU-26)."""
        ...

    @abstractmethod
    async def listar(
        self,
        tipo_evento: str | None = None,
        device_id: str | None = None,
        chain_id: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        limite: int = 100,
        offset: int = 0,
    ) -> list[RegistroTrazabilidad]: ...
