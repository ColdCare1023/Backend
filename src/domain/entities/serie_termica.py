"""HU-31 / HU-36: serie térmica agregada por intervalos (buckets).

El dashboard y el Historial pedían lecturas crudas con un tope (5000 y 200):
a ~10 s por lectura, eso cubría unas 14 h y 33 min de la ventana pedida. La
serie agregada resume TODO el periodo en a lo sumo unos cientos o miles de
intervalos, con mínimo, máximo y promedio, sin perder la forma de la curva ni
las excursiones (el máximo y el mínimo de cada intervalo se conservan).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True, frozen=True)
class EstadisticaMagnitud:
    """Mínimo, máximo y promedio de una magnitud en un intervalo. `n` cuenta
    solo las lecturas con valor (un sensor caído no aporta un 0 inventado)."""

    minimo: float | None = None
    maximo: float | None = None
    promedio: float | None = None
    n: int = 0


@dataclass(slots=True, frozen=True)
class BucketSerie:
    inicio: datetime
    fin: datetime
    lecturas: int
    temperatura_interna: EstadisticaMagnitud
    temperatura_ambiental: EstadisticaMagnitud
    humedad_ambiental: EstadisticaMagnitud
    # Lecturas con temperatura interna fuera de 2-8 °C (regla HU-21) y con la
    # excursión confirmada por el pipeline: el intervalo puede promediar dentro
    # de rango y aun así contener una excursión.
    fuera_de_rango: int = 0
    excursiones: int = 0


@dataclass(slots=True, frozen=True)
class ResumenSerie:
    lecturas: int
    temperatura_interna: EstadisticaMagnitud
    temperatura_ambiental: EstadisticaMagnitud
    humedad_ambiental: EstadisticaMagnitud
    fuera_de_rango: int = 0
    excursiones: int = 0
    primera_lectura_en: datetime | None = None
    ultima_lectura_en: datetime | None = None
    # Lecturas del nodo cuyo instante de captura se desconoce (arranque sin
    # hora sincronizada y sin reanclar, HU-07): su `timestamp` es la hora de
    # llegada al backend, así que no se dibujan en la curva.
    lecturas_sin_hora: int = 0
    # BE-001: lecturas registradas a mano; no se mezclan con la telemetría.
    lecturas_manuales: int = 0
    por_riesgo_efectivo: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class BrechaSerie:
    """Tramo sin telemetría entre dos lecturas consecutivas (o en los bordes
    del periodo) mayor que el umbral de silencio del nodo (HU-54)."""

    desde: datetime
    hasta: datetime
    causa: str = "sin_datos"
    perdidas: int | None = None
    abierta: bool = False

    @property
    def duracion_segundos(self) -> int:
        return max(0, int((self.hasta - self.desde).total_seconds()))


@dataclass(slots=True, frozen=True)
class SerieTermica:
    device_id: str
    desde: datetime
    hasta: datetime
    bucket_segundos: int
    bucket_automatico: bool
    intervalo_reporte_segundos: int
    umbral_brecha_segundos: int
    buckets: list[BucketSerie]
    resumen: ResumenSerie
    brechas: list[BrechaSerie]
    brechas_truncadas: bool = False
