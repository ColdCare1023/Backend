from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.domain.value_objects.rango_termico import RANGO_TERMICO_BPA
from src.infrastructure.ai.features import FeaturesRiesgoTermico

DURACION_PREVENTIVA_MINUTOS = 10.0
TENDENCIA_CRITICA = 1.5
MARGEN_PREVENTIVO_C = 1.0


def clasificar_por_regla(features: FeaturesRiesgoTermico) -> NivelRiesgo:
    """Clasificación preventiva binaria (HU-16/17/18 del backlog de 54 HU):
    normal | riesgo_preventivo. Usada para etiquetar el dataset sintético de
    entrenamiento y como salvaguarda determinista de consistencia en runtime
    (ver README sección 7).

    Nunca produce EXCURSION_CRITICA: esa clase es exclusiva de la regla
    determinista de rango 2-8 °C aplicada directamente sobre la temperatura
    actual (LecturaTermica.es_excursion_confirmada/calcular_riesgo_efectivo),
    independiente de la IA. Mezclar ambas aquí volvería a acoplar lo que el
    backlog exige mantener separado — el modelo nunca "fabrica" una excursión
    crítica, solo anticipa condiciones preventivas."""
    temp = features.temperatura_interna
    fuera_de_rango = not RANGO_TERMICO_BPA.contiene(temp)

    if fuera_de_rango:
        return NivelRiesgo.RIESGO_PREVENTIVO

    distancia = RANGO_TERMICO_BPA.distancia_al_limite(temp)
    if (
        distancia <= MARGEN_PREVENTIVO_C
        or features.duracion_fuera_rango >= DURACION_PREVENTIVA_MINUTOS
        or abs(features.tendencia_termica) >= TENDENCIA_CRITICA
        or features.frecuencia_desviaciones >= 3
    ):
        return NivelRiesgo.RIESGO_PREVENTIVO

    return NivelRiesgo.NORMAL
