"""HU-48: último `estado_nodo` de cada ESP32 y episodios de buffer saturado.

Hasta ahora el `estado_nodo` (cada 5 min) solo actualizaba el latido y se
reenviaba por SSE, y `buffer_saturado` solo quedaba en la bitácora: con la
página recién abierta, el tablero no podía mostrar los datos pendientes en
LittleFS ni la brecha por capacidad agotada y su causa.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(slots=True)
class EstadoNodo:
    """Último `estado_nodo` aceptado de un dispositivo (una fila por nodo)."""

    device_id: str
    recibido_en: datetime
    ocurrido_en: datetime | None = None
    boot_id: int | None = None
    pendientes: int | None = None
    capacidad: int | None = None
    ocupacion_pct: int | None = None
    perdidas_total: int | None = None
    cuarentena: int | None = None
    # Rango temporal de los registros pendientes, si el firmware lo informa
    # (`pendientes_desde` / `pendientes_hasta` en el `detail`).
    pendientes_desde: datetime | None = None
    pendientes_hasta: datetime | None = None
    # Último `estado_nodo` con `pendientes = 0`: el nodo había sincronizado todo.
    ultima_sincronizacion_en: datetime | None = None
    detalle: dict | None = None


@dataclass(slots=True)
class EpisodioBufferSaturado:
    """Periodo en que LittleFS estaba lleno y el nodo no pudo almacenar
    muestras nuevas (HU-06 esc. 3, HU-48 esc. 2). Lo identifica la primera
    muestra perdida (`desde_boot_id`, `desde_seq_no`)."""

    id: UUID
    device_id: str
    causa: str
    perdidas: int
    abierto: bool
    registrado_en: datetime
    actualizado_en: datetime
    desde: datetime | None = None
    hasta: datetime | None = None
    desde_boot_id: int | None = None
    desde_seq_no: int | None = None
    hasta_boot_id: int | None = None
    hasta_seq_no: int | None = None
    perdidas_total: int | None = None
    cerrado_en: datetime | None = None


class IEstadoNodoRepository(ABC):
    @abstractmethod
    async def obtener_estado(self, device_id: str) -> EstadoNodo | None: ...

    @abstractmethod
    async def guardar_estado(self, estado: EstadoNodo) -> EstadoNodo:
        """Reemplaza el último estado del nodo (upsert por `device_id`)."""
        ...

    @abstractmethod
    async def episodio_por_inicio(
        self, device_id: str, desde_boot_id: int, desde_seq_no: int
    ) -> EpisodioBufferSaturado | None: ...

    @abstractmethod
    async def episodios_abiertos(self, device_id: str) -> list[EpisodioBufferSaturado]: ...

    @abstractmethod
    async def agregar_episodio(self, episodio: EpisodioBufferSaturado) -> EpisodioBufferSaturado: ...

    @abstractmethod
    async def actualizar_episodio(self, episodio: EpisodioBufferSaturado) -> EpisodioBufferSaturado: ...

    @abstractmethod
    async def listar_episodios(
        self,
        device_id: str,
        limite: int = 10,
        desde: datetime | None = None,
        hasta: datetime | None = None,
    ) -> list[EpisodioBufferSaturado]:
        """Más recientes primero. Con `desde`/`hasta`, solo los que se solapan
        con ese periodo (o no tienen hora)."""
        ...
