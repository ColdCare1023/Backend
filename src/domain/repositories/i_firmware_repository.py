from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID


class IFirmwareRepository(ABC):
    @abstractmethod
    async def crear_release(
        self,
        version: str,
        hash_sha256: str,
        descripcion: str,
        fecha_compilacion: datetime,
        url_descarga: str | None = None,
        tamano_bytes: int | None = None,
    ) -> dict: ...

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

    @abstractmethod
    async def obtener_despliegue_enviado(self, device_id: str, version: str | None) -> dict | None:
        """IOT-004: despliegue más reciente con la orden OTA ya enviada al nodo
        (de esa versión, o de cualquiera si `version` es None)."""
        ...
