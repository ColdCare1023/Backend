from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(slots=True)
class VersionModeloIA:
    """HU-46: metadatos de gobierno y reproducibilidad de una versión del
    modelo Random Forest registrada y aprobada."""

    version: str
    model_hash: str
    feature_schema_version: str
    dataset_version: str
    scikit_learn_version: str
    python_version: str
    trained_at: datetime
    aprobado_por: UUID
    id: UUID | None = None
    aprobado_en: datetime | None = None
    activa: bool = False
    activada_en: datetime | None = None
    desactivada_en: datetime | None = None
