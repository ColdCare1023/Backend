from abc import ABC, abstractmethod
from datetime import datetime

from src.domain.entities.version_modelo_ia import VersionModeloIA


class IModeloIARepository(ABC):
    @abstractmethod
    async def registrar(self, version: VersionModeloIA) -> VersionModeloIA: ...

    @abstractmethod
    async def obtener_por_version(self, version: str) -> VersionModeloIA | None: ...

    @abstractmethod
    async def obtener_activa(self) -> VersionModeloIA | None: ...

    @abstractmethod
    async def activar(self, version: str, cuando: datetime) -> VersionModeloIA:
        """Desactiva la versión activa anterior (si existe, registrando
        desactivada_en) y activa `version` (debe existir ya registrada —
        responsabilidad del caso de uso verificarlo antes)."""
        ...

    @abstractmethod
    async def desactivar(self, version: str, cuando: datetime) -> VersionModeloIA | None:
        """P-12: desactiva `version` SOLO si es la activa (activa=False,
        desactivada_en=cuando), de forma atómica: de dos llamadas simultáneas
        solo una la encuentra activa. Devuelve la versión desactivada, o None
        si no estaba activa (no cambia nada)."""
        ...

    @abstractmethod
    async def listar(self) -> list[VersionModeloIA]: ...

    @abstractmethod
    async def obtener_version_activa_en(self, momento: datetime) -> VersionModeloIA | None:
        """HU-47 criterio 2: qué versión estaba activa en un instante pasado
        (para verificar que la versión usada en una inferencia histórica era
        la aprobada/activa vigente en ese momento)."""
        ...
