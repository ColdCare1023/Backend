from abc import ABC, abstractmethod
from datetime import datetime

from src.domain.entities.prediccion_preventiva import PrediccionPreventiva


class IPrediccionPreventivaRepository(ABC):
    @abstractmethod
    async def agregar(self, prediccion: PrediccionPreventiva) -> PrediccionPreventiva: ...

    @abstractmethod
    async def listar(
        self,
        device_id: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        limite: int = 100,
    ) -> list[PrediccionPreventiva]: ...
