from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from src.domain.value_objects.nivel_riesgo import NivelRiesgo


@dataclass(slots=True)
class PrediccionPreventiva:
    """Salida del modelo preventivo temporal para una lectura.

    Es una PREDICCIÓN, no una medición: vive en su propia tabla
    (`predicciones_preventivas`), nunca sobrescribe los valores capturados por
    el sensor y no participa de la cadena hash de lecturas.

    `clase` solo puede ser NORMAL o RIESGO_PREVENTIVO (el modelo es binario);
    EXCURSION_CRITICA la decide la regla determinista de rango. `clase`,
    `puntuacion` y `umbral` son None salvo cuando `estado == "ok"`.
    """

    device_id: str
    timestamp_inferencia: datetime
    modo: str
    estado: str
    horizonte_min: int | None = None
    clase: NivelRiesgo | None = None
    puntuacion: float | None = None
    umbral: float | None = None
    puntuacion_calibrada: bool = False
    modelo_version: str | None = None
    modelo_sha256: str | None = None
    motivo: str | None = None
    vector_features: dict[str, float] | None = None
    lectura_id: UUID | None = None
    id: UUID | None = None
    creado_en: datetime | None = None
