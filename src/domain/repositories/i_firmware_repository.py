from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID


class IFirmwareRepository(ABC):
    @abstractmethod
    async def crear_release(self, version: str, hash_sha256: str, descripcion: str, fecha_compilacion: datetime) -> dict: ...

    @abstractmethod
    async def obtener_release(self, version: str) -> dict | None: ...

    @abstractmethod
    async def listar_releases(self) -> list[dict]: ...

    @abstractmethod
    async def crear_despliegue(self, device_id: str, version_objetivo: str, programado_para: datetime | None) -> dict: ...

    @abstractmethod
    async def obtener_despliegue(self, despliegue_id: UUID) -> dict | None: ...

    @abstractmethod
    async def actualizar_despliegue(
        self, despliegue_id: UUID, estado: str, resultado: str | None, completado_en: datetime | None
    ) -> dict: ...
