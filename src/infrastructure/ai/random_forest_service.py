import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

import joblib

from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.ai.features import FEATURE_NAMES, FeaturesRiesgoTermico
from src.infrastructure.ai.reglas_riesgo import clasificar_por_regla

logger = logging.getLogger("infrastructure.ai.random_forest_service")

# HU-18 criterio 4 (backlog 54 HU): el modelo Random Forest es un
# clasificador BINARIO — nunca utiliza excursion_critica como clase. Esa
# clase es exclusiva de la regla determinista de rango 2-8 °C, separada del
# pipeline de IA (ver reglas_riesgo.clasificar_por_regla). Un artefacto que
# declare excursion_critica entre sus clases se rechaza al cargar: no es un
# modelo compatible con este contrato, sin importar su exactitud.
_CLASES_ESPERADAS = frozenset({NivelRiesgo.NORMAL.value, NivelRiesgo.RIESGO_PREVENTIVO.value})

DEFAULT_MODEL_PATH = Path(__file__).parent / "models" / "random_forest_termico.pkl"
DEFAULT_METRICS_PATH = Path(__file__).parent / "models" / "training_metrics.json"
# AIV-06: metadata externa (nunca embebida junto al hash que la referencia),
# archivo hermano del .pkl oficial. Ver train_model_v3.py.
DEFAULT_METADATA_PATH = Path(__file__).parent / "models" / "model_metadata.json"

_SEVERIDAD = {
    NivelRiesgo.NORMAL: 0,
    NivelRiesgo.RIESGO_PREVENTIVO: 1,
    NivelRiesgo.EXCURSION_CRITICA: 2,
}

# origen_clasificacion: quién decidió el nivel de riesgo.
ORIGEN_MODELO = "random_forest"
ORIGEN_REGLA_SALVAGUARDA = "salvaguarda_determinista"
# Corrige el hallazgo B-05/AIV-03 de la auditoría: un sensor sin lectura o con
# valor no finito (None/NaN/inf) no debe tratarse como 0.0 °C ni llegar al
# modelo. Estos dos orígenes distinguen POR QUÉ no hubo inferencia real.
ORIGEN_DATO_INSUFICIENTE = "dato_insuficiente"
ORIGEN_FALLO_SENSOR = "fallo_sensor"

# estado_inferencia: AIV-07 — elimina la ambigüedad de confianza_ia=0.0.
ESTADO_COMPLETADA = "completada"
ESTADO_OMITIDA = "omitida"
ESTADO_FALLIDA = "fallida"
ESTADO_MODELO_NO_DISPONIBLE = "modelo_no_disponible"


@dataclass(frozen=True, slots=True)
class ResultadoInferencia:
    """Resultado de la clasificación de riesgo con evidencia de cómo se obtuvo.

    confianza: probabilidad que el bosque asigna a la clase elegida. `None`
    (nunca `0.0` como valor centinela, corrige AIV-07) cuando no se ejecutó
    ninguna inferencia de modelo. Una salvaguarda determinista puede definir
    nivel de riesgo, pero tampoco inventa una probabilidad de Random Forest.

    nivel: None cuando no fue posible construir features válidas (sensor
    ausente/inválido) — la lectura se persiste igual (RF-07) pero no se
    clasifica ni genera alerta.

    estado_inferencia: completada | omitida | fallida | modelo_no_disponible.
    motivo_no_inferencia: texto breve sin trazas de pila ni secretos, solo
    cuando estado_inferencia != completada.
    """

    nivel: NivelRiesgo | None
    confianza: float | None
    origen: str
    estado_inferencia: str = ESTADO_COMPLETADA
    motivo_no_inferencia: str | None = None
    # HU-47: evidencia completa de la inferencia para auditoría — probabilidad
    # por cada clase (no solo la ganadora) y el vector de features exacto que
    # se le pasó al modelo. None cuando no hubo inferencia real de Random
    # Forest (regla directa, modelo no disponible, fallo_sensor).
    probabilidades_por_clase: dict[str, float] | None = None
    vector_features: dict[str, float] | None = None
    # HU-20: lo que dicta la salvaguarda determinista por sí sola. Con el
    # modelo legado en modo experimental es lo único que decide el riesgo
    # efectivo cuando no hay excursión confirmada.
    nivel_regla: NivelRiesgo | None = None


class RandomForestRiesgoService:
    """Carga perezosa (lazy) del modelo joblib e inferencia de riesgo térmico.

    Si el modelo entrenado no está disponible (p. ej. entorno recién clonado sin
    ejecutar train_model.py), degrada de forma controlada a la regla térmica base
    en lugar de fallar el arranque del backend.

    Salvaguarda determinista (supervisión de la IA, ver README sección 7): la
    predicción del modelo nunca puede REBAJAR la severidad que dicta la regla
    BPA 2-8 °C. Si la regla detecta un riesgo mayor, prevalece la regla y el
    resultado queda marcado con origen `regla_salvaguarda`. Esto acota el daño
    de un falso negativo del modelo sin renunciar a su capacidad de anticipar
    riesgos que la regla no ve.
    """

    def __init__(
        self,
        model_path: Path | None = None,
        metrics_path: Path | None = None,
        metadata_path: Path | None = None,
    ) -> None:
        self._model_path = model_path or DEFAULT_MODEL_PATH
        self._metrics_path = metrics_path or DEFAULT_METRICS_PATH
        # Un artefacto inyectado para prueba/v1 no debe heredar por accidente
        # metadata del modelo oficial. Sin ruta explícita, busca sólo su
        # archivo hermano; el oficial conserva su ruta conocida.
        self._metadata_path = metadata_path or (
            DEFAULT_METADATA_PATH if model_path is None else self._model_path.with_name("model_metadata.json")
        )
        self._modelo = None
        self._metadata: dict | None = None
        self._lock = Lock()

    def _validar_compatibilidad(self, modelo, metadata: dict | None) -> None:
        """Comprueba, al cargar, que el artefacto declara el checksum, las
        features y las clases esperadas (hallazgos AI-04/AI-05 de la
        auditoría). No revalida el checksum del archivo en sí en cada carga
        (sería redundante con la integridad del propio filesystem), pero sí
        exige que la metadata declare `model_hash` y que el estimador cargado
        sea consistente con el contrato de features/clases del backend."""
        if metadata is None:
            logger.warning(
                "Modelo cargado sin metadata (formato v1 sin versión embebida); "
                "no se puede validar compatibilidad de features/clases/checksum."
            )
            return
        if not metadata.get("model_hash"):
            logger.warning("El artefacto no declara model_hash — no auditable (hallazgo AI-05).")
        features_declaradas = metadata.get("feature_names")
        if features_declaradas is not None and list(features_declaradas) != list(FEATURE_NAMES):
            raise RuntimeError(
                "El modelo cargado fue entrenado con un orden de features distinto al "
                f"que espera el backend. Esperado: {list(FEATURE_NAMES)}. "
                f"Declarado en metadata: {list(features_declaradas)}."
            )
        n_features_modelo = getattr(modelo, "n_features_in_", None)
        if n_features_modelo is not None and n_features_modelo != len(FEATURE_NAMES):
            raise RuntimeError(
                f"El modelo espera {n_features_modelo} features pero el backend "
                f"construye {len(FEATURE_NAMES)}."
            )
        clases_modelo = getattr(modelo, "classes_", None)
        if clases_modelo is not None and not set(clases_modelo).issubset(_CLASES_ESPERADAS):
            raise RuntimeError(
                f"El modelo predice clases desconocidas: {set(clases_modelo) - _CLASES_ESPERADAS}. "
                f"Esperadas: {sorted(_CLASES_ESPERADAS)}."
            )

    def _validar_checksum_externo(self, metadata: dict) -> None:
        """Valida v3 antes de deserializarlo.

        Sólo metadata externa activa esta validación estricta: v1 no tiene
        metadata y v2 conserva su hash histórico circular deliberadamente.
        """
        esperado = metadata.get("model_hash")
        actual = hashlib.sha256(self._model_path.read_bytes()).hexdigest()
        if not isinstance(esperado, str) or actual != esperado:
            raise RuntimeError(
                "Checksum SHA-256 inválido para el artefacto del modelo; "
                "se rechaza antes de deserializarlo."
            )

    def _cargar_modelo(self):
        if self._modelo is None:
            with self._lock:
                if self._modelo is None and self._model_path.exists():
                    metadata_externa = None
                    if self._metadata_path.exists():
                        metadata_externa = json.loads(self._metadata_path.read_text(encoding="utf-8"))
                        self._validar_checksum_externo(metadata_externa)
                    artefacto = joblib.load(self._model_path)
                    # Formato v2: dict {"modelo", "metadata"} (metadata embebida,
                    # con la circularidad de auto-referencia documentada en
                    # AIV-06); v3: estimador puro + model_metadata.json externo
                    # (AIV-06 corregido, sin re-serializar tras el hash); v1: el
                    # estimador a secas, sin metadata alguna.
                    if isinstance(artefacto, dict):
                        modelo = artefacto["modelo"]
                        metadata = artefacto.get("metadata")
                    else:
                        modelo = artefacto
                        metadata = metadata_externa
                    self._validar_compatibilidad(modelo, metadata)
                    self._modelo = modelo
                    self._metadata = metadata
        return self._modelo

    @property
    def modelo_disponible(self) -> bool:
        return self._cargar_modelo() is not None

    @property
    def metadata(self) -> dict | None:
        """Metadatos de entrenamiento embebidos en el artefacto (versión, fecha, seed)."""
        self._cargar_modelo()
        return self._metadata

    def metricas_entrenamiento(self) -> dict | None:
        """Métricas del último entrenamiento (accuracy, precision, recall, F1 por
        clase, matriz de confusión y validación cruzada) — evidencia RNF-04."""
        if not self._metrics_path.exists():
            return None
        return json.loads(self._metrics_path.read_text(encoding="utf-8"))

    def inferir(self, features: FeaturesRiesgoTermico) -> ResultadoInferencia:
        modelo = self._cargar_modelo()
        nivel_regla = clasificar_por_regla(features)

        if modelo is None:
            return ResultadoInferencia(
                nivel=nivel_regla,
                confianza=None,
                origen=ORIGEN_REGLA_SALVAGUARDA,
                estado_inferencia=ESTADO_MODELO_NO_DISPONIBLE,
                motivo_no_inferencia="modelo_no_cargado_en_este_entorno",
                nivel_regla=nivel_regla,
            )

        try:
            probabilidades = modelo.predict_proba([features.to_array()])[0]
        except Exception as exc:  # defensa en profundidad: nunca propagar un fallo de predict_proba sin marca clara
            logger.exception("Fallo al ejecutar predict_proba del Random Forest.")
            return ResultadoInferencia(
                nivel=nivel_regla,
                confianza=None,
                origen=ORIGEN_REGLA_SALVAGUARDA,
                estado_inferencia=ESTADO_FALLIDA,
                motivo_no_inferencia=f"excepcion_en_inferencia:{type(exc).__name__}",
                nivel_regla=nivel_regla,
            )

        indice = int(probabilidades.argmax())
        nivel_modelo = NivelRiesgo(modelo.classes_[indice])
        confianza = float(probabilidades[indice])
        # HU-47: probabilidad por CADA clase que el modelo conoce, no solo la
        # ganadora — necesario para que una auditoría explique la decisión.
        probabilidades_por_clase = {
            str(clase): float(prob) for clase, prob in zip(modelo.classes_, probabilidades, strict=True)
        }
        vector_features = dict(zip(FEATURE_NAMES, features.to_array(), strict=True))

        if _SEVERIDAD[nivel_regla] > _SEVERIDAD[nivel_modelo]:
            # Regla determina nivel, no probabilidad Random Forest. Nunca
            # presentar 100 % como confianza de IA inexistente. Se conserva
            # igual la evidencia de lo que el modelo predijo (HU-47), aunque
            # su clase no haya prevalecido.
            return ResultadoInferencia(
                nivel_regla, None, ORIGEN_REGLA_SALVAGUARDA, ESTADO_COMPLETADA,
                probabilidades_por_clase=probabilidades_por_clase, vector_features=vector_features,
                nivel_regla=nivel_regla,
            )
        return ResultadoInferencia(
            nivel_modelo, confianza, ORIGEN_MODELO, ESTADO_COMPLETADA,
            probabilidades_por_clase=probabilidades_por_clase, vector_features=vector_features,
            nivel_regla=nivel_regla,
        )

    def inferir_solo_regla(self, features: FeaturesRiesgoTermico) -> ResultadoInferencia:
        """HU-20 con IA_LEGADO_MODO=desactivado: el modelo no se carga ni se
        ejecuta; decide solo la salvaguarda determinista."""
        nivel_regla = clasificar_por_regla(features)
        return ResultadoInferencia(
            nivel=nivel_regla,
            confianza=None,
            origen=ORIGEN_REGLA_SALVAGUARDA,
            estado_inferencia=ESTADO_MODELO_NO_DISPONIBLE,
            motivo_no_inferencia="modelo_legado_desactivado",
            nivel_regla=nivel_regla,
        )

    def predecir(self, features: FeaturesRiesgoTermico) -> NivelRiesgo:
        return self.inferir(features).nivel


_instancia: RandomForestRiesgoService | None = None


def get_random_forest_service() -> RandomForestRiesgoService:
    global _instancia
    if _instancia is None:
        _instancia = RandomForestRiesgoService()
    return _instancia
