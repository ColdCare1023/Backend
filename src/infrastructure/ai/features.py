from dataclasses import dataclass

FEATURE_NAMES: tuple[str, ...] = (
    "temperatura_ambiental",
    "humedad_ambiental",
    "temperatura_interna",
    "diferencia_sensores",
    "duracion_fuera_rango",
    "frecuencia_desviaciones",
    "tendencia_termica",
    "apertura_refrigerador",
    "hora_evento",
    "estado_conectividad_online",
)


@dataclass(frozen=True, slots=True)
class FeaturesRiesgoTermico:
    """Vector de entrada al modelo Random Forest (10 variables, ver README sección 7)."""

    temperatura_ambiental: float
    humedad_ambiental: float
    temperatura_interna: float
    diferencia_sensores: float
    duracion_fuera_rango: float
    frecuencia_desviaciones: float
    tendencia_termica: float
    apertura_refrigerador: bool
    hora_evento: int
    estado_conectividad_online: bool

    def to_array(self) -> list[float]:
        return [
            self.temperatura_ambiental,
            self.humedad_ambiental,
            self.temperatura_interna,
            self.diferencia_sensores,
            self.duracion_fuera_rango,
            self.frecuencia_desviaciones,
            self.tendencia_termica,
            float(self.apertura_refrigerador),
            float(self.hora_evento),
            float(self.estado_conectividad_online),
        ]
