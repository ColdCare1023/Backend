import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from numbers import Real
from uuid import UUID

from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.domain.value_objects.rango_termico import RANGO_TERMICO_BPA

# B-10 — ventana temporal admisible de una lectura entrante.
#
# Futuro: tolerancia mínima, solo para absorber la deriva del reloj del ESP32
# frente al servidor. Un dispositivo no puede reportar el futuro, y aceptarlo
# permitiría "adelantar" registros para desplazar el orden de la cadena hash.
DERIVA_FUTURO_MAXIMA = timedelta(minutes=10)
#
# Pasado: generoso a propósito. Cuando el nodo pierde conectividad almacena las
# lecturas y las reenvía al reconectar; una ventana corta (p. ej. 2 horas)
# descartaría en silencio datos legítimos de una caída de red nocturna, que es
# justo el escenario donde la evidencia térmica más importa. El límite existe
# para rechazar valores absurdos (relojes sin sincronizar, epoch 1970), no
# para acotar el reenvío normal.
ANTIGUEDAD_MAXIMA = timedelta(hours=48)


@dataclass(slots=True)
class LecturaTermica:
    """Lectura térmica capturada por el nodo edge (ESP32 + SHT31 + DS18B20)."""

    device_id: str
    timestamp: datetime
    temperatura_ambiental: float | None
    humedad_ambiental: float | None
    temperatura_interna: float | None
    # HU-04: None cuando el dispositivo no tiene MC-38 instalado — no se
    # simula una puerta cerrada que en realidad no existe.
    apertura_refrigerador: bool | None
    estado_conectividad: str
    id: UUID | None = None
    nivel_riesgo: NivelRiesgo | None = None
    payload: dict | None = None
    # Evidencia de la inferencia de IA (RNF-04, corrige hallazgo AI-06): con
    # qué versión de modelo, confianza y origen (modelo/regla/sin dato) se
    # clasificó esta lectura. None cuando aún no se clasificó.
    modelo_version: str | None = None
    confianza_ia: float | None = None
    origen_clasificacion: str | None = None
    # AIV-07: estado real de la inferencia (completada/omitida/fallida/
    # modelo_no_disponible) y motivo breve cuando no fue "completada" — nunca
    # contiene trazas de pila ni secretos, solo un código corto.
    estado_inferencia: str | None = None
    motivo_no_inferencia: str | None = None
    # HU-05: identificador lógico de la lectura generado por el firmware
    # (None en payloads antiguos sin este campo) y versión del contrato de
    # payload con el que se construyó.
    reading_id: str | None = None
    schema_version: int | None = None
    # HU-01/HU-11 (backlog 54 HU): identidad lógica real para idempotencia.
    # None en payloads de firmware que aún no los declara (ver reading_id).
    boot_id: int | None = None
    seq_no: int | None = None
    # HU-01: calidad de sincronización temporal en el instante de captura.
    time_quality: str | None = None
    # HU-05: instante en que el backend recibió el mensaje (distinto de
    # `timestamp`=captured_at, el instante de captura declarado por el nodo).
    received_at: datetime | None = None
    # HU-18/21/34: `nivel_riesgo` (arriba) sigue siendo la clase cruda que
    # produce el pipeline IA/salvaguarda (model_class). `excursion_confirmada`
    # y `riesgo_efectivo` son conceptos DISTINTOS y deliberadamente separados
    # (Observación 5 del backlog de 51 HU): una excursión confirmada depende
    # ÚNICAMENTE de que la temperatura válida esté fuera de 2-8 °C, nunca de
    # lo que diga la IA; `riesgo_efectivo` es la política determinista que
    # combina ambas para decidir qué se muestra/alerta.
    excursion_confirmada: bool = False
    riesgo_efectivo: NivelRiesgo | None = None
    # HU-47: evidencia completa de la inferencia para auditoría — None cuando
    # no hubo inferencia real de Random Forest.
    probabilidades_ia: dict[str, float] | None = None
    vector_features_ia: dict[str, float] | None = None

    def es_excursion_confirmada(self) -> bool:
        """HU-21: regla directa de rango térmico, inmediata e independiente de
        la IA — sin duración mínima ni tendencia, a diferencia de la
        salvaguarda determinista de `reglas_riesgo.clasificar_por_regla`
        (esa alimenta `nivel_riesgo`/model_class, no esta excursión)."""
        if self.temperatura_interna is None:
            return False
        return not RANGO_TERMICO_BPA.contiene(self.temperatura_interna)

    def calcular_riesgo_efectivo(self) -> NivelRiesgo | None:
        """HU-18/21/34: una excursión confirmada por rango es EXCURSION_CRITICA
        sin importar qué haya clasificado la IA (incluso si `nivel_riesgo` es
        None por no_clasificable). Si no hay excursión confirmada, el riesgo
        efectivo es el que produjo el pipeline IA/salvaguarda tal cual."""
        if self.es_excursion_confirmada():
            return NivelRiesgo.EXCURSION_CRITICA
        return self.nivel_riesgo

    def diferencia_sensores(self) -> float:
        if self.temperatura_ambiental is None or self.temperatura_interna is None:
            return 0.0
        return self.temperatura_ambiental - self.temperatura_interna

    def es_lectura_valida(self) -> bool:
        """Validación básica de rango plausible antes de persistir/clasificar."""
        for valor, minimo, maximo in (
            (self.temperatura_ambiental, -40.0, 125.0),
            (self.temperatura_interna, -55.0, 125.0),
            (self.humedad_ambiental, 0.0, 100.0),
        ):
            if valor is not None and (
                not isinstance(valor, Real)
                or isinstance(valor, bool)
                or not math.isfinite(valor)
                or not minimo <= valor <= maximo
            ):
                return False
        return True

    @staticmethod
    def es_timestamp_valido(
        timestamp: datetime, ahora: datetime | None = None
    ) -> tuple[bool, str]:
        """B-10: verifica que el instante declarado por el dispositivo sea
        plausible. Devuelve (válido, motivo) para que quien llame pueda
        auditar el motivo exacto del rechazo."""
        ahora = ahora or datetime.now(tz=timezone.utc)
        # SQLite devuelve datetimes naive aunque la columna sea aware; se
        # asume UTC, igual criterio que `timestamp_canonico` en la cadena hash.
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        if timestamp > ahora + DERIVA_FUTURO_MAXIMA:
            return False, "timestamp_futuro"
        if timestamp < ahora - ANTIGUEDAD_MAXIMA:
            return False, "timestamp_demasiado_antiguo"
        return True, "ok"
