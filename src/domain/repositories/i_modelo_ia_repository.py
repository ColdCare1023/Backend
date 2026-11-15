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
    async def bloquear_cambio_de_version(self) -> None:
        """Serializa los cambios de versión activa (activar y desactivar)
        hasta que termina la transacción. Los casos de uso lo llaman PRIMERO,
        antes de leer qué versión está activa y de tomar la hora del cambio:
        así la historia encadenada que consulta HU-47 esc. 2 y las filas no
        divergen, y las horas de los cambios no retroceden. Sin efecto donde
        la base ya serializa las escrituras (SQLite)."""
        ...

    @abstractmethod
    async def activar(self, version: str, cuando: datetime) -> VersionModeloIA:
        """Desactiva la versión activa anterior (si existe, registrando
        desactivada_en) y activa `version` (debe existir ya registrada —
        responsabilidad del caso de uso verificarlo antes). El llamador ya
        tomó bloquear_cambio_de_version() y calculó `cuando` después."""
        ...

    @abstractmethod
    async def desactivar(self, version: str, cuando: datetime) -> VersionModeloIA | None:
        """P-12: desactiva `version` SOLO si es la activa (activa=False,
        desactivada_en=cuando), de forma atómica: de dos llamadas simultáneas
        solo una la encuentra activa. Devuelve la versión desactivada, o None
        si no estaba activa (no cambia nada). Como en activar(), el llamador
        ya tomó bloquear_cambio_de_version()."""
        ...

    @abstractmethod
    async def listar(self) -> list[VersionModeloIA]: ...

    @abstractmethod
    async def obtener_version_activa_en(self, momento: datetime) -> VersionModeloIA | None:
        """HU-47 criterio 2: qué versión estaba activa en un instante pasado
        (para verificar que la versión usada en una inferencia histórica era
        la aprobada/activa vigente en ese momento)."""
        ...
