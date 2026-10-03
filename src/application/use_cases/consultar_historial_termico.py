from datetime import datetime

from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.repositories.i_lectura_repository import ILecturaRepository


class ConsultarHistorialTermicoUseCase:
    """RF-12: filtra el historial de lecturas por fecha, dispositivo, conectividad y riesgo."""

    def __init__(self, lectura_repository: ILecturaRepository) -> None:
        self._lectura_repository = lectura_repository

    async def execute(
        self,
        device_id: str | None = None,
        nivel_riesgo: str | None = None,
        estado_conectividad: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        limite: int = 100,
        offset: int = 0,
    ) -> list[LecturaTermica]:
        return await self._lectura_repository.listar(
            device_id=device_id,
            nivel_riesgo=nivel_riesgo,
            estado_conectividad=estado_conectividad,
            desde=desde,
            hasta=hasta,
            limite=limite,
            offset=offset,
        )
