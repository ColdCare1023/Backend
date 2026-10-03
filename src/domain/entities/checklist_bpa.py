from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from src.domain.exceptions import DomainError

# HU-37: los diez ítems verificables del Manual de Buenas Prácticas de
# Almacenamiento (RM N.º 132-2015/MINSA y modificatorias). El orden y las
# claves son el contrato con el frontend (i18n `checklist.items.*`); cambiarlas
# rompe la traducción y el histórico ya persistido.
ITEMS_CHECKLIST_BPA: tuple[str, ...] = (
    "temperatura",
    "termometro",
    "registros",
    "alertas_revisadas",
    "acciones_documentadas",
    "puerta",
    "limpieza",
    "exclusivo",
    "rotulado",
    "respaldo",
)


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass(slots=True)
class ChecklistBPA:
    """Verificación diaria de Buenas Prácticas de Almacenamiento (HU-37).

    Un checklist por usuario y fecha: volver a guardar el mismo día actualiza
    el registro existente en vez de crear uno nuevo, pero cada guardado deja su
    propio eslabón en la cadena de trazabilidad SHA-256 (RF-14), de modo que la
    corrección de un ítem queda auditable y no borra la declaración anterior.
    """

    usuario_id: UUID
    fecha: str  # ISO date "YYYY-MM-DD"
    temperatura: bool
    termometro: bool
    registros: bool
    alertas_revisadas: bool
    acciones_documentadas: bool
    puerta: bool
    limpieza: bool
    exclusivo: bool
    rotulado: bool
    respaldo: bool
    observaciones: str | None = None
    id: UUID | None = None
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    def items(self) -> dict[str, bool]:
        """Los diez ítems como diccionario, en el orden canónico declarado."""
        return {clave: getattr(self, clave) for clave in ITEMS_CHECKLIST_BPA}

    def validar(self) -> None:
        """Ningún ítem puede quedar sin declarar: un checklist a medias no es
        evidencia válida de cumplimiento ante una inspección."""
        faltantes = [clave for clave, valor in self.items().items() if valor is None]
        if faltantes:
            raise DomainError(
                "Todos los ítems del checklist deben estar declarados; faltan: "
                + ", ".join(faltantes)
            )

    def total_conformes(self) -> int:
        return sum(1 for valor in self.items().values() if valor)

    def es_conforme(self) -> bool:
        """Conforme solo si los diez ítems se declararon como cumplidos."""
        return self.total_conformes() == len(ITEMS_CHECKLIST_BPA)
