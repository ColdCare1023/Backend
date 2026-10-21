"""IOT-007: anomalías del reloj del nodo (SNTP no autenticado).

El nodo ya rechaza saltos de hora dentro de un arranque, pero la primera
sincronización de cada arranque no tiene referencia local. El backend sí:

- `received_at`: una lectura no puede haberse capturado DESPUÉS de recibirse.
  Un `captured_at` por delante más allá de la tolerancia delata un reloj
  adelantado (respuesta SNTP manipulada o error).
- Las lecturas sincronizadas previas del MISMO arranque: la relación
  época↔uptime solo cambia por la deriva del oscilador. Un salto mayor que la
  tolerancia delata una hora movida a mitad de arranque.

Las anomalías se auditan; la lectura se conserva (la temperatura no está en
duda, su hora sí) y queda señalada para revisión.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.domain.entities.lectura_termica import LecturaTermica

TOLERANCIA_RELOJ = timedelta(seconds=120)
CALIDAD_SINCRONIZADA = "synced"


@dataclass(frozen=True, slots=True)
class AnomaliaReloj:
    accion: str
    detalle: dict = field(default_factory=dict)


def _utc(valor: datetime) -> datetime:
    return valor if valor.tzinfo is not None else valor.replace(tzinfo=timezone.utc)


def _base(lectura: LecturaTermica) -> datetime:
    return _utc(lectura.timestamp) - timedelta(milliseconds=lectura.uptime_ms or 0)


def _sincronizada(lectura: LecturaTermica | None) -> bool:
    return (
        lectura is not None
        and lectura.time_quality == CALIDAD_SINCRONIZADA
        and lectura.uptime_ms is not None
        and lectura.boot_id is not None
    )


def anomalias_de_reloj(lectura: LecturaTermica, previa: LecturaTermica | None) -> list[AnomaliaReloj]:
    if not _sincronizada(lectura):
        return []
    identidad = {"device_id": lectura.device_id, "boot_id": lectura.boot_id, "seq_no": lectura.seq_no}
    anomalias: list[AnomaliaReloj] = []

    if lectura.received_at is not None:
        adelanto = _utc(lectura.timestamp) - _utc(lectura.received_at)
        if adelanto > TOLERANCIA_RELOJ:
            anomalias.append(
                AnomaliaReloj("RELOJ_NODO_ADELANTADO", {**identidad, "adelanto_s": int(adelanto.total_seconds())})
            )

    if _sincronizada(previa) and previa.boot_id == lectura.boot_id:
        salto = _base(lectura) - _base(previa)
        if abs(salto) > TOLERANCIA_RELOJ:
            anomalias.append(
                AnomaliaReloj(
                    "SALTO_RELOJ_NODO",
                    {**identidad, "seq_no_previa": previa.seq_no, "salto_s": int(salto.total_seconds())},
                )
            )
    return anomalias
