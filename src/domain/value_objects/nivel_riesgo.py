from enum import StrEnum


class NivelRiesgo(StrEnum):
    NORMAL = "normal"
    RIESGO_PREVENTIVO = "riesgo_preventivo"
    EXCURSION_CRITICA = "excursion_critica"
