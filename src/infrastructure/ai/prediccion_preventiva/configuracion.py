"""Parámetros compartidos por el entrenamiento offline y la inferencia.

Un único lugar para el horizonte, la rejilla temporal y las ventanas: si el
extractor de entrenamiento y el de producción leyeran constantes distintas,
el modelo vería en producción una distribución que nunca aprendió.

El rango 2-8 °C NO se redefine aquí: se reutiliza `RANGO_TERMICO_BPA` del
dominio, la misma regla que decide `excursion_confirmada` en el backend.
"""

from dataclasses import dataclass, field

from src.domain.value_objects.rango_termico import RANGO_TERMICO_BPA, RangoTermico


@dataclass(frozen=True, slots=True)
class ConfiguracionPreventiva:
    # Horizonte preventivo: parámetro experimental propuesto, no una verdad
    # demostrada (ver docs/ml/03_LABELING_METHODOLOGY.md).
    horizonte_min: int = 30
    # Rejilla regular a la que se remuestrea toda fuente (5 s en Hohenheim,
    # ~60 s en el hospital, 10-3600 s en el ESP32). Cada punto `g` vale la
    # media de las lecturas en (g - agregacion, g]: solo pasado, nunca futuro.
    paso_rejilla_s: int = 60
    # Agregación algo mayor que el paso: con muestreo de 60 s y ±1 s de
    # fluctuación del reloj, una agregación de exactamente 60 s deja puntos
    # vacíos alternados (dos lecturas en un punto, cero en el siguiente).
    agregacion_s: int = 90
    ventanas_min: tuple[int, ...] = (5, 10, 15)
    # Un hueco mayor que este corta la serie en segmentos independientes:
    # ninguna ventana ni etiqueta atraviesa un corte de conectividad.
    hueco_max_s: int = 180
    # Fracción mínima de puntos válidos exigida dentro de cada ventana de
    # features y dentro del horizonte para afirmar "no hubo excursión".
    cobertura_min_ventana: float = 0.8
    cobertura_min_horizonte: float = 0.9
    # Dos tramos fuera de rango separados por menos de esto son el MISMO
    # episodio (una oscilación alrededor de 8 °C no son cien excursiones).
    fusion_episodios_min: int = 10
    rango: RangoTermico = field(default=RANGO_TERMICO_BPA)

    @property
    def ventana_max_min(self) -> int:
        return max(self.ventanas_min)

    @property
    def puntos_ventana(self) -> int:
        """Puntos de rejilla que necesita la ventana más larga, incluido el actual."""
        return self.ventana_max_min * 60 // self.paso_rejilla_s + 1

    @property
    def puntos_horizonte(self) -> int:
        return self.horizonte_min * 60 // self.paso_rejilla_s

    def como_dict(self) -> dict:
        return {
            "horizonte_min": self.horizonte_min,
            "paso_rejilla_s": self.paso_rejilla_s,
            "agregacion_s": self.agregacion_s,
            "ventanas_min": list(self.ventanas_min),
            "hueco_max_s": self.hueco_max_s,
            "cobertura_min_ventana": self.cobertura_min_ventana,
            "cobertura_min_horizonte": self.cobertura_min_horizonte,
            "fusion_episodios_min": self.fusion_episodios_min,
            "rango_min_c": self.rango.minimo_celsius,
            "rango_max_c": self.rango.maximo_celsius,
        }


CONFIGURACION_POR_DEFECTO = ConfiguracionPreventiva()


def configuracion_desde_manifiesto(manifiesto: dict) -> ConfiguracionPreventiva:
    """Reconstruye la configuración con la que se entrenó una versión. La
    inferencia usa ESTA configuración, no la por defecto del código."""
    c = manifiesto["configuracion"]
    config = ConfiguracionPreventiva(
        horizonte_min=c["horizonte_min"],
        paso_rejilla_s=c["paso_rejilla_s"],
        agregacion_s=c["agregacion_s"],
        ventanas_min=tuple(c["ventanas_min"]),
        hueco_max_s=c["hueco_max_s"],
        cobertura_min_ventana=c["cobertura_min_ventana"],
        cobertura_min_horizonte=c["cobertura_min_horizonte"],
        fusion_episodios_min=c["fusion_episodios_min"],
    )
    if (config.rango.minimo_celsius, config.rango.maximo_celsius) != (c["rango_min_c"], c["rango_max_c"]):
        raise ValueError("El rango térmico del manifiesto no coincide con el del dominio (2-8 °C)")
    return config

# Estados conceptuales del ciclo de vida de un modelo (sección 13 del
# mandato). Entrenar con éxito NO equivale a aprobar para producción.
ESTADO_EXPERIMENTAL = "EXPERIMENTAL"
ESTADO_VALIDADO_OFFLINE = "VALIDATED_OFFLINE"
ESTADO_APROBADO = "APPROVED_FOR_DEPLOYMENT"
ESTADO_RECHAZADO = "REJECTED_INSUFFICIENT_EVIDENCE"
ESTADOS_MODELO = (ESTADO_EXPERIMENTAL, ESTADO_VALIDADO_OFFLINE, ESTADO_APROBADO, ESTADO_RECHAZADO)

# Resultado de una inferencia preventiva, en orden de precedencia. Solo
# CALIDAD_OK lleva clase y puntuación: ningún otro estado fabrica una
# predicción. SIN_DATOS (ausencia de telemetría) no aparece aquí porque sin
# lectura no hay inferencia: lo calcula el estado operativo del dispositivo
# (dispositivos_router, 3 ciclos perdidos).
CALIDAD_OK = "ok"
CALIDAD_SENSOR_FAULT = "SENSOR_FAULT"
CALIDAD_EXCURSION_EN_CURSO = "EXCURSION_EN_CURSO"
CALIDAD_MODELO_NO_DISPONIBLE = "MODELO_NO_DISPONIBLE"
CALIDAD_HISTORIAL_INSUFICIENTE = "HISTORIAL_INSUFICIENTE"
CALIDAD_FALLO_INFERENCIA = "FALLO_INFERENCIA"
