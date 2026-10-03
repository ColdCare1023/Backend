from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RangoTermico:
    """Rango de conservación de medicamentos termolábiles: 2 C - 8 C."""

    minimo_celsius: float = 2.0
    maximo_celsius: float = 8.0

    def __post_init__(self) -> None:
        if self.minimo_celsius >= self.maximo_celsius:
            raise ValueError("minimo_celsius debe ser menor que maximo_celsius")

    def contiene(self, temperatura: float) -> bool:
        return self.minimo_celsius <= temperatura <= self.maximo_celsius

    def distancia_al_limite(self, temperatura: float) -> float:
        """Distancia (positiva) al límite más cercano del rango. 0 si está dentro."""
        if self.contiene(temperatura):
            return min(temperatura - self.minimo_celsius, self.maximo_celsius - temperatura)
        if temperatura < self.minimo_celsius:
            return self.minimo_celsius - temperatura
        return temperatura - self.maximo_celsius


RANGO_TERMICO_BPA = RangoTermico(minimo_celsius=2.0, maximo_celsius=8.0)
