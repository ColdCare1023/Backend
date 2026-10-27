from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

TIPO_SIN_DATOS = "sin_datos"
# HU-44: rotación por compromiso. La telemetría está retenida (monitoreo
# suspendido) o una lectura retenida salió de 2-8 °C con origen no verificado.
TIPO_MONITOREO_SUSPENDIDO = "monitoreo_suspendido"
TIPO_EXCURSION_NO_VERIFICADA = "excursion_no_verificada"


@dataclass(slots=True)
class IncidenteOperativo:
    """HU-54: episodio de silencio de un nodo (SIN_DATOS). HU-44: también la
    retención por compromiso y sus excursiones de origen no verificado."""

    id: UUID
    device_id: str
    tipo: str
    abierto_en: datetime
    ultimo_reporte_en: datetime | None
    umbral_segundos: int
    resuelto_en: datetime | None = None
    notificado_en: datetime | None = None
    notificacion_intentos: int = 0
    detalle: dict | None = None

    @property
    def activo(self) -> bool:
        return self.resuelto_en is None


class IIncidenteRepository(ABC):
    @abstractmethod
    async def abierto(self, device_id: str, tipo: str = TIPO_SIN_DATOS) -> IncidenteOperativo | None: ...

    @abstractmethod
    async def abrir(
        self, device_id: str, abierto_en: datetime, ultimo_reporte_en: datetime | None, umbral_segundos: int,
        tipo: str = TIPO_SIN_DATOS, detalle: dict | None = None,
    ) -> IncidenteOperativo: ...

    @abstractmethod
    async def resolver(self, incidente_id: UUID, cuando: datetime) -> IncidenteOperativo: ...

    @abstractmethod
    async def obtener(self, incidente_id: UUID) -> IncidenteOperativo | None: ...

    @abstractmethod
    async def listar(self, device_id: str | None = None, limite: int = 100) -> list[IncidenteOperativo]: ...

    @abstractmethod
    async def listar_pendientes_de_notificar(self, desde: datetime, max_intentos: int) -> list[IncidenteOperativo]: ...

    @abstractmethod
    async def marcar_notificado(self, incidente_id: UUID, cuando: datetime) -> None: ...

    @abstractmethod
    async def registrar_intento_fallido(self, incidente_id: UUID) -> int: ...
