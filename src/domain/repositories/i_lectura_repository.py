from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from src.domain.entities.lectura_termica import LecturaTermica


class ILecturaRepository(ABC):
    @abstractmethod
    async def agregar(self, lectura: LecturaTermica) -> LecturaTermica: ...

    @abstractmethod
    async def obtener_por_id(self, lectura_id: UUID) -> LecturaTermica | None: ...

    @abstractmethod
    async def listar(
        self,
        device_id: str | None = None,
        nivel_riesgo: str | None = None,
        estado_conectividad: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        limite: int = 100,
        offset: int = 0,
    ) -> list[LecturaTermica]: ...

    @abstractmethod
    async def listar_recientes_por_device(self, device_id: str, limite: int) -> list[LecturaTermica]: ...

    @abstractmethod
    async def obtener_por_device_y_timestamp(
        self, device_id: str, timestamp: datetime
    ) -> LecturaTermica | None:
        """Deduplicación/idempotencia de compatibilidad (RF-07): localiza una
        lectura ya persistida para el mismo dispositivo y el mismo instante
        exacto. Usada cuando el payload no trae boot_id/seq_no (firmware
        anterior); ver obtener_por_device_boot_seq() para la clave preferida."""
        ...

    @abstractmethod
    async def obtener_por_device_boot_seq(
        self, device_id: str, boot_id: int, seq_no: int
    ) -> LecturaTermica | None:
        """HU-11: idempotencia real por identidad lógica de la lectura
        (device_id+boot_id+seq_no) — clave preferida sobre
        obtener_por_device_y_timestamp cuando el payload la declara."""
        ...
