from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.entities.prediccion_preventiva import PrediccionPreventiva
from src.domain.repositories.i_lectura_repository import ILecturaRepository
from src.domain.repositories.i_prediccion_preventiva_repository import IPrediccionPreventivaRepository
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.ai.prediccion_preventiva.configuracion import (
    CALIDAD_EXCURSION_EN_CURSO,
    CALIDAD_HISTORIAL_INSUFICIENTE,
    CALIDAD_OK,
    CALIDAD_SENSOR_FAULT,
)
from src.infrastructure.ai.prediccion_preventiva.servicio import MODO_ACTIVO, ServicioPrediccionPreventiva
from src.infrastructure.ai.random_forest_service import (
    ESTADO_COMPLETADA,
    ESTADO_OMITIDA,
    ORIGEN_DATO_INSUFICIENTE,
    ORIGEN_FALLO_SENSOR,
    ORIGEN_MODELO,
    ORIGEN_REGLA_SALVAGUARDA,
)

# La ventana más larga es de 15 min (+90 s de agregación). Con el intervalo
# mínimo del ESP32 (10 s) eso son ~100 lecturas; con el de fábrica (30 s), 33.
LIMITE_HISTORIAL_PREVENTIVO = 120

# Estados sin predicción que, en modo activo, SÍ sustituyen a la clasificación
# legada (no hay clase que inventar). MODELO_NO_DISPONIBLE y FALLO_INFERENCIA
# no están: en esos casos se conserva la clasificación existente.
_ORIGEN_POR_ESTADO = {
    CALIDAD_EXCURSION_EN_CURSO: ORIGEN_REGLA_SALVAGUARDA,
    CALIDAD_SENSOR_FAULT: ORIGEN_FALLO_SENSOR,
    CALIDAD_HISTORIAL_INSUFICIENTE: ORIGEN_DATO_INSUFICIENTE,
}


class PredecirRiesgoPreventivoUseCase:
    """Predicción preventiva temporal de una lectura entrante.

    - Modo sombra: se calcula y se guarda en `predicciones_preventivas`; no
      cambia `nivel_riesgo`, alertas, SSE ni la cadena hash.
    - Modo activo (solo con un modelo APPROVED_FOR_DEPLOYMENT): además decide
      `nivel_riesgo` (normal / riesgo_preventivo). La excursión crítica sigue
      siendo exclusiva de la regla determinista (`riesgo_efectivo`).

    Las lecturas manuales y las simuladas de dispositivos demo nunca se
    predicen: no son telemetría real del DS18B20.
    """

    def __init__(
        self,
        servicio: ServicioPrediccionPreventiva,
        lectura_repository: ILecturaRepository,
        prediccion_repository: IPrediccionPreventivaRepository,
    ) -> None:
        self._servicio = servicio
        self._lectura_repository = lectura_repository
        self._prediccion_repository = prediccion_repository

    @property
    def modo(self) -> str:
        return self._servicio.modo

    async def predecir(self, lectura: LecturaTermica) -> PrediccionPreventiva | None:
        if not self._servicio.habilitado or lectura.es_manual or (lectura.payload or {}).get("simulated"):
            return None
        historial = await self._lectura_repository.listar_recientes_por_device(
            lectura.device_id, limite=LIMITE_HISTORIAL_PREVENTIVO, solo_dispositivo=True
        )
        return self._servicio.predecir(lectura, historial)

    def aplicar_a_lectura(self, lectura: LecturaTermica, prediccion: PrediccionPreventiva | None) -> bool:
        """Solo en modo activo. Devuelve True si la predicción reemplazó la
        clasificación legada de la lectura."""
        if self.modo != MODO_ACTIVO or prediccion is None:
            return False
        if prediccion.estado == CALIDAD_OK:
            lectura.nivel_riesgo = prediccion.clase
            lectura.origen_clasificacion = ORIGEN_MODELO
            lectura.estado_inferencia = ESTADO_COMPLETADA
            lectura.motivo_no_inferencia = None
            lectura.modelo_version = prediccion.modelo_version
            lectura.vector_features_ia = prediccion.vector_features
            # Una puntuación no calibrada no se presenta como probabilidad.
            if prediccion.puntuacion_calibrada and prediccion.puntuacion is not None:
                p = prediccion.puntuacion
                lectura.probabilidades_ia = {NivelRiesgo.NORMAL.value: 1 - p, NivelRiesgo.RIESGO_PREVENTIVO.value: p}
                lectura.confianza_ia = round(p if prediccion.clase == NivelRiesgo.RIESGO_PREVENTIVO else 1 - p, 4)
            else:
                lectura.probabilidades_ia = None
                lectura.confianza_ia = None
            return True
        if prediccion.estado in _ORIGEN_POR_ESTADO:
            lectura.nivel_riesgo = None
            lectura.confianza_ia = None
            lectura.probabilidades_ia = None
            lectura.vector_features_ia = None
            lectura.modelo_version = prediccion.modelo_version
            lectura.origen_clasificacion = _ORIGEN_POR_ESTADO[prediccion.estado]
            lectura.estado_inferencia = ESTADO_OMITIDA
            lectura.motivo_no_inferencia = (prediccion.motivo or prediccion.estado)[:100]
            return True
        return False

    async def registrar(self, prediccion: PrediccionPreventiva, lectura_id) -> PrediccionPreventiva:
        prediccion.lectura_id = lectura_id
        return await self._prediccion_repository.agregar(prediccion)
