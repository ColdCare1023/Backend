from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.entities.serie_termica import BucketSerie, ResumenSerie


class ILecturaRepository(ABC):
    @abstractmethod
    async def agregar(self, lectura: LecturaTermica) -> LecturaTermica: ...

    async def agregar_si_no_existe(self, lectura: LecturaTermica) -> tuple[LecturaTermica, bool]:
        """HU-11/HU-22: inserta la lectura o, si otra transacción ya insertó la
        misma identidad, devuelve esa. (lectura, True) solo si la creó esta."""
        return await self.agregar(lectura), True

    @abstractmethod
    async def obtener_por_id(self, lectura_id: UUID) -> LecturaTermica | None: ...

    @abstractmethod
    async def listar(
        self,
        device_id: str | None = None,
        nivel_riesgo: str | None = None,
        estado_conectividad: str | None = None,
        riesgo_efectivo: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        limite: int = 100,
        offset: int = 0,
    ) -> list[LecturaTermica]: ...

    @abstractmethod
    async def listar_recientes_por_device(
        self, device_id: str, limite: int, solo_dispositivo: bool = True
    ) -> list[LecturaTermica]: ...

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

    async def estadisticas_periodo(
        self,
        device_id: str | None,
        desde: datetime | None,
        hasta: datetime | None,
        minimo_celsius: float,
        maximo_celsius: float,
    ) -> dict | None:
        """HU-38: agregados de TODO el periodo (no de una muestra recortada):
        total de lecturas, con temperatura interna, en rango, mín/máx/promedio
        y lecturas por riesgo efectivo. None si el repositorio no lo soporta
        (el llamador calcula sobre las lecturas que ya tiene)."""
        return None

    # ── HU-31 / HU-36: serie agregada ─────────────────────────────────────
    # No abstractos: una implementación que no los necesite (p. ej. un doble
    # de pruebas) sigue siendo instanciable. La hora de cada lectura en la
    # serie es la de captura: `timestamp_estimado` si el backend la reancló,
    # `timestamp` si no; las lecturas `unsynced` sin reanclar no tienen hora
    # de captura y quedan fuera de los intervalos (se cuentan aparte).

    async def contar(
        self,
        device_id: str | None = None,
        nivel_riesgo: str | None = None,
        estado_conectividad: str | None = None,
        riesgo_efectivo: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
    ) -> int:
        """Total de filas que `listar()` devolvería con los mismos filtros y
        sin límite (la interfaz avisa del recorte con él)."""
        raise NotImplementedError

    async def agregar_serie(
        self, device_id: str, desde: datetime, hasta: datetime, bucket_segundos: int
    ) -> list[BucketSerie]:
        """Intervalos alineados a múltiplos de `bucket_segundos` (época UNIX)
        con al menos una lectura del nodo con hora; los vacíos no se devuelven."""
        raise NotImplementedError

    async def resumir_periodo(self, device_id: str, desde: datetime, hasta: datetime) -> ResumenSerie:
        raise NotImplementedError

    async def detectar_huecos(
        self, device_id: str, desde: datetime, hasta: datetime, umbral_segundos: int, limite: int
    ) -> list[tuple[datetime, datetime]]:
        """Pares (lectura anterior, lectura siguiente) separados por más de
        `umbral_segundos`, en orden cronológico, como mucho `limite`."""
        raise NotImplementedError

    async def ultima_hora_de_captura(self, device_id: str) -> datetime | None:
        """Hora de captura más reciente persistida del nodo (HU-48: los
        pendientes en LittleFS son posteriores a ella)."""
        raise NotImplementedError
