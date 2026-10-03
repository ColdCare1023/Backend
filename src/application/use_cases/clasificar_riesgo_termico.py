import math
from dataclasses import replace
from datetime import datetime, timezone
from numbers import Real

import numpy as np

from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.value_objects.rango_termico import RANGO_TERMICO_BPA
from src.infrastructure.ai.features import FeaturesRiesgoTermico
from src.infrastructure.ai.random_forest_service import (
    ESTADO_OMITIDA,
    ORIGEN_DATO_INSUFICIENTE,
    ORIGEN_FALLO_SENSOR,
    RandomForestRiesgoService,
    ResultadoInferencia,
)

UMBRAL_DESVIACION_C = 0.5
# HU-17: se requieren al menos 2 temperaturas internas previas válidas para
# calcular una tendencia térmica real (regresión lineal). Con menos, no se
# imputa un valor neutro (0.0) — eso produciría el mismo training-serving
# skew que la historia prohíbe explícitamente para la humedad — se marca la
# lectura como no_clasificable y queda sujeta únicamente a la regla directa
# de rango 2-8 °C (HU-21), que no depende de este historial.
MINIMO_HISTORIAL_PARA_TENDENCIA = 2


def _a_utc(valor: datetime) -> datetime:
    """SQLite devuelve datetimes naive aunque la columna sea timezone=True;
    se asumen UTC para poder operar contra timestamps aware."""
    return valor if valor.tzinfo is not None else valor.replace(tzinfo=timezone.utc)


def _es_invalido(valor: object | None) -> bool:
    """NaN e infinito nunca deben llegar al modelo (AIV-03). `None` se trata
    aparte (ausencia, no invalidez de tipo)."""
    return valor is not None and (not isinstance(valor, Real) or isinstance(valor, bool) or not math.isfinite(valor))


def _ultimo_valor_valido(historial_ordenado: list[LecturaTermica], campo: str) -> float | None:
    for h in reversed(historial_ordenado):
        valor = getattr(h, campo)
        if valor is not None and math.isfinite(valor):
            return valor
    return None


class ClasificarRiesgoTermicoUseCase:
    """Enriquece la lectura con features derivadas del historial y ejecuta el
    modelo Random Forest (ver README secciones 7 y 15)."""

    def __init__(self, ai_service: RandomForestRiesgoService) -> None:
        self._ai_service = ai_service

    @property
    def modelo_version(self) -> str | None:
        """Versión del modelo actualmente cargado (hallazgo AI-06: persistida
        por lectura para poder auditar retroactivamente con qué versión se
        clasificó cada registro histórico)."""
        metadata = self._ai_service.metadata
        return metadata.get("model_version") if metadata else None

    def _construir_features(
        self, lectura: LecturaTermica, historial: list[LecturaTermica]
    ) -> FeaturesRiesgoTermico:
        temperatura_interna = lectura.temperatura_interna or 0.0

        historial_ordenado = sorted(historial, key=lambda lec: _a_utc(lec.timestamp))
        temperaturas_previas = [
            h.temperatura_interna for h in historial_ordenado if h.temperatura_interna is not None
        ]

        duracion_fuera_rango = 0.0
        if historial_ordenado:
            for h in reversed(historial_ordenado):
                if h.temperatura_interna is None or RANGO_TERMICO_BPA.contiene(h.temperatura_interna):
                    break
                delta = (_a_utc(lectura.timestamp) - _a_utc(h.timestamp)).total_seconds() / 60.0
                duracion_fuera_rango = max(duracion_fuera_rango, abs(delta))

        frecuencia_desviaciones = sum(
            1
            for t in temperaturas_previas
            if not RANGO_TERMICO_BPA.contiene(t)
        )

        tendencia_termica = 0.0
        if len(temperaturas_previas) >= 2:
            x = np.arange(len(temperaturas_previas), dtype=float)
            y = np.array(temperaturas_previas, dtype=float)
            pendiente, _ = np.polyfit(x, y, 1)
            tendencia_termica = float(pendiente)

        return FeaturesRiesgoTermico(
            temperatura_ambiental=lectura.temperatura_ambiental,
            humedad_ambiental=lectura.humedad_ambiental,
            temperatura_interna=temperatura_interna,
            diferencia_sensores=lectura.diferencia_sensores(),
            duracion_fuera_rango=duracion_fuera_rango,
            frecuencia_desviaciones=float(frecuencia_desviaciones),
            tendencia_termica=tendencia_termica,
            # HU-04: ausencia de MC-38 es un hecho de configuración de
            # hardware, no un dato faltante — False neutro no afecta la IA,
            # tal como exige HU-04.
            apertura_refrigerador=(
                lectura.apertura_refrigerador
                if lectura.apertura_refrigerador is not None
                else False
            ),
            hora_evento=lectura.timestamp.hour,
            estado_conectividad_online=lectura.estado_conectividad == "online",
        )

    def execute(
        self, lectura: LecturaTermica, historial: list[LecturaTermica]
    ) -> ResultadoInferencia:
        """Guard completo de sensores (AIV-03). Nunca convierte `None` en
        `0.0`; nunca deja pasar NaN/infinito al modelo; distingue sensor
        crítico ausente/inválido (fallo_sensor, bloquea inferencia) de dato
        secundario ausente sin historial de respaldo (dato_insuficiente,
        también bloquea) de dato secundario ausente CON respaldo en
        historial (aplica fallback documentado, la inferencia continúa)."""
        interna = lectura.temperatura_interna
        ambiental = lectura.temperatura_ambiental

        # Corrige el hallazgo B-05/AIV-03: la temperatura interna (sensor
        # crítico BPA 2-8 °C) ausente o con valor no finito nunca se
        # sustituye por 0.0 °C. Sin este dato la lectura no es clasificable.
        if interna is None:
            return ResultadoInferencia(
                nivel=None, confianza=None, origen=ORIGEN_FALLO_SENSOR,
                estado_inferencia=ESTADO_OMITIDA, motivo_no_inferencia="sensor_interno_ausente",
            )
        if _es_invalido(interna):
            return ResultadoInferencia(
                nivel=None, confianza=None, origen=ORIGEN_FALLO_SENSOR,
                estado_inferencia=ESTADO_OMITIDA, motivo_no_inferencia="sensor_interno_valor_no_finito",
            )
        if _es_invalido(ambiental):
            return ResultadoInferencia(
                nivel=None, confianza=None, origen=ORIGEN_FALLO_SENSOR,
                estado_inferencia=ESTADO_OMITIDA, motivo_no_inferencia="sensor_ambiental_valor_no_finito",
            )
        if _es_invalido(lectura.humedad_ambiental):
            return ResultadoInferencia(
                nivel=None, confianza=None, origen=ORIGEN_FALLO_SENSOR,
                estado_inferencia=ESTADO_OMITIDA, motivo_no_inferencia="sensor_humedad_valor_no_finito",
            )

        historial_ordenado = sorted(historial, key=lambda lec: _a_utc(lec.timestamp))

        if ambiental is None:
            # Solo un sensor válido (el crítico): se aplica el fallback
            # documentado (último valor ambiental válido del historial) en
            # vez de inventar 0.0 °C. Si tampoco hay historial disponible,
            # los datos son insuficientes y no se ejecuta inferencia.
            ambiental_fallback = _ultimo_valor_valido(historial_ordenado, "temperatura_ambiental")
            if ambiental_fallback is None:
                return ResultadoInferencia(
                    nivel=None, confianza=None, origen=ORIGEN_DATO_INSUFICIENTE,
                    estado_inferencia=ESTADO_OMITIDA,
                    motivo_no_inferencia="sensor_ambiental_ausente_sin_historial_de_respaldo",
                )
            lectura = replace(lectura, temperatura_ambiental=ambiental_fallback)

        if lectura.humedad_ambiental is None:
            # HU-17: mismo tratamiento que la temperatura ambiental. Antes se
            # sustituía por un 50 % fijo sin importar si había o no historial
            # de respaldo — exactamente la imputación de valor neutro que la
            # historia prohíbe. Ahora usa el mismo fallback documentado
            # (último valor válido del historial) y, si tampoco existe, la
            # lectura queda no_clasificable en vez de inventar el dato.
            humedad_fallback = _ultimo_valor_valido(historial_ordenado, "humedad_ambiental")
            if humedad_fallback is None:
                return ResultadoInferencia(
                    nivel=None, confianza=None, origen=ORIGEN_DATO_INSUFICIENTE,
                    estado_inferencia=ESTADO_OMITIDA,
                    motivo_no_inferencia="sensor_humedad_ausente_sin_historial_de_respaldo",
                )
            lectura = replace(lectura, humedad_ambiental=humedad_fallback)

        temperaturas_previas_validas = sum(
            1 for h in historial_ordenado
            if h.temperatura_interna is not None and math.isfinite(h.temperatura_interna)
        )
        if temperaturas_previas_validas < MINIMO_HISTORIAL_PARA_TENDENCIA:
            # HU-17 criterio 2 (caso explícito del enunciado: "el nodo acaba
            # de conectarse y no hay suficiente historial térmico reciente
            # para calcular... tendencia térmica móvil"). No se inyecta 0.0
            # como tendencia neutra: se marca no_clasificable y la lectura
            # queda sujeta únicamente a la regla directa de rango 2-8 °C
            # (HU-21), que LecturaTermica.es_excursion_confirmada() evalúa
            # de forma completamente independiente de este resultado.
            return ResultadoInferencia(
                nivel=None, confianza=None, origen=ORIGEN_DATO_INSUFICIENTE,
                estado_inferencia=ESTADO_OMITIDA,
                motivo_no_inferencia="historial_insuficiente_para_tendencia",
            )

        features = self._construir_features(lectura, historial)
        return self._ai_service.inferir(features)
