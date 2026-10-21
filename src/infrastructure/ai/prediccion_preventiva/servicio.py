"""Servicio de inferencia del modelo preventivo temporal.

Precedencia (docs/ml/06_BACKEND_INTEGRATION.md), evaluada por lectura:

1. Validez del dato: sin temperatura interna válida → SENSOR_FAULT.
2. Excursión actual (regla determinista 2-8 °C) → EXCURSION_EN_CURSO: el
   clasificador preventivo no se ejecuta; la alerta crítica la genera el
   flujo existente sin depender de este servicio.
3. Modelo cargado, verificado y compatible → si no, MODELO_NO_DISPONIBLE.
4. Historial suficiente (ventana de 15 min con cobertura ≥ 80 %, reloj
   sincronizado) → si no, HISTORIAL_INSUFICIENTE.
5. Features con el MISMO extractor del entrenamiento e inferencia.

Nunca lanza: cualquier fallo se convierte en un estado explícito, para que
la ingesta de telemetría siga funcionando aunque el modelo no cargue.
"""

import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

import numpy as np
import sklearn

from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.entities.prediccion_preventiva import PrediccionPreventiva
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.ai.prediccion_preventiva import registro
from src.infrastructure.ai.prediccion_preventiva.configuracion import (
    CALIDAD_EXCURSION_EN_CURSO,
    CALIDAD_FALLO_INFERENCIA,
    CALIDAD_HISTORIAL_INSUFICIENTE,
    CALIDAD_MODELO_NO_DISPONIBLE,
    CALIDAD_OK,
    CALIDAD_SENSOR_FAULT,
    ESTADO_APROBADO,
    ESTADO_EXPERIMENTAL,
    ESTADO_VALIDADO_OFFLINE,
    configuracion_desde_manifiesto,
)
from src.infrastructure.ai.prediccion_preventiva.features import features_en_instante, nombres_features

logger = logging.getLogger("infrastructure.ai.prediccion_preventiva.servicio")

MODO_DESACTIVADO = "desactivado"
MODO_SOMBRA = "sombra"
MODO_ACTIVO = "activo"
MODOS = (MODO_DESACTIVADO, MODO_SOMBRA, MODO_ACTIVO)

# En sombra el modelo no decide nada: se admite uno experimental para
# acumular evidencia prospectiva (Experimento D). En activo SOLO uno aprobado.
ESTADOS_ADMITIDOS = {
    MODO_SOMBRA: frozenset({ESTADO_EXPERIMENTAL, ESTADO_VALIDADO_OFFLINE, ESTADO_APROBADO}),
    MODO_ACTIVO: frozenset({ESTADO_APROBADO}),
}
TIEMPO_NO_SINCRONIZADO = "unsynced"


_EPOCA = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _a_ns(ts: datetime) -> int:
    """Nanosegundos UTC con aritmética entera (sin redondeo de float).
    SQLite devuelve datetimes naive: se asumen UTC, como en el resto del backend."""
    ts = ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)
    return (ts - _EPOCA) // timedelta(microseconds=1) * 1000


def _version_menor(v: str) -> tuple[str, ...]:
    return tuple(v.split(".")[:2])


class ServicioPrediccionPreventiva:
    def __init__(
        self,
        modo: str,
        version: str | None,
        sha256_esperado: str | None = None,
        raiz: Path = registro.RAIZ_REGISTRO,
    ) -> None:
        if modo not in MODOS:
            raise ValueError(f"Modo preventivo desconocido: {modo!r}")
        self.modo = modo
        self._version = version or None
        self._sha256 = sha256_esperado or None
        self._raiz = raiz
        self._modelo = None
        self._manifiesto: dict | None = None
        self._config = None
        self._motivo_no_disponible: str | None = None
        self._intentado = False
        self._lock = Lock()

    @property
    def habilitado(self) -> bool:
        return self.modo != MODO_DESACTIVADO

    def _cargar(self) -> None:
        if self._intentado:
            return
        with self._lock:
            if self._intentado:
                return
            self._intentado = True
            try:
                self._modelo, self._manifiesto, self._config = self._cargar_y_validar()
            except Exception as exc:  # nunca propagar: la telemetría no depende del modelo
                self._motivo_no_disponible = str(exc)[:200]
                logger.warning("Modelo preventivo no disponible (%s): %s", self.modo, self._motivo_no_disponible)

    def _cargar_y_validar(self):
        if not self.habilitado:
            raise RuntimeError("modo_desactivado")
        if self._version is None:
            raise RuntimeError("IA_PREVENTIVO_VERSION no está configurada")
        modelo, manifiesto = registro.cargar_verificado(self._version, self._raiz, self._sha256)
        if manifiesto["estado"] not in ESTADOS_ADMITIDOS[self.modo]:
            raise RuntimeError(
                f"La versión {self._version} está en estado {manifiesto['estado']}; el modo {self.modo} "
                f"exige {sorted(ESTADOS_ADMITIDOS[self.modo])}"
            )
        entrenado_con = manifiesto["versiones"]["scikit_learn"]
        if _version_menor(entrenado_con) != _version_menor(sklearn.__version__):
            raise RuntimeError(f"Modelo entrenado con scikit-learn {entrenado_con}; instalado {sklearn.__version__}")
        config = configuracion_desde_manifiesto(manifiesto)
        esperadas = nombres_features(config, manifiesto["conjunto_features"])
        if manifiesto["features"] != esperadas or getattr(modelo, "n_features_in_", None) != len(esperadas):
            raise RuntimeError("Las features del modelo no coinciden con las del extractor")
        if [int(c) for c in getattr(modelo, "classes_", [])] != [0, 1]:
            raise RuntimeError("El modelo no es el clasificador binario normal(0)/riesgo_preventivo(1)")
        return modelo, manifiesto, config

    @property
    def disponible(self) -> bool:
        self._cargar()
        return self._modelo is not None

    def estado(self) -> dict:
        """Resumen para el endpoint de evidencia (sin rutas ni secretos)."""
        self._cargar()
        if self._modelo is None:
            return {"modo": self.modo, "disponible": False, "version": self._version, "motivo": self._motivo_no_disponible}
        m = self._manifiesto
        return {
            "modo": self.modo,
            "disponible": True,
            "version": m["version"],
            "estado_modelo": m["estado"],
            "motivo_estado": m.get("motivo_estado"),
            "sha256": m["artefacto"]["sha256"],
            "horizonte_min": m["configuracion"]["horizonte_min"],
            "conjunto_features": m["conjunto_features"],
            "features": m["features"],
            "umbral_decision": m["umbral_decision"],
            "puntuacion_calibrada": m.get("puntuacion_calibrada", False),
            "fuente_entrenamiento": m.get("fuente_entrenamiento"),
            "metricas_prueba": m.get("metricas_prueba"),
            "criterios_aceptacion": m.get("criterios_aceptacion"),
            "entrenado_en": m.get("entrenado_en"),
        }

    def _resultado(self, lectura: LecturaTermica, estado: str, motivo: str | None = None, **extra) -> PrediccionPreventiva:
        manifiesto = self._manifiesto or {}
        return PrediccionPreventiva(
            device_id=lectura.device_id,
            timestamp_inferencia=lectura.timestamp,
            modo=self.modo,
            estado=estado,
            motivo=motivo,
            horizonte_min=(manifiesto.get("configuracion") or {}).get("horizonte_min"),
            modelo_version=manifiesto.get("version", self._version),
            modelo_sha256=(manifiesto.get("artefacto") or {}).get("sha256"),
            puntuacion_calibrada=bool(manifiesto.get("puntuacion_calibrada", False)),
            **extra,
        )

    def predecir(self, lectura: LecturaTermica, historial: list[LecturaTermica]) -> PrediccionPreventiva:
        interna = lectura.temperatura_interna
        if interna is None or isinstance(interna, bool) or not math.isfinite(interna):
            return self._resultado(lectura, CALIDAD_SENSOR_FAULT, "sensor_interno_ausente_o_invalido")
        if lectura.es_excursion_confirmada():
            return self._resultado(lectura, CALIDAD_EXCURSION_EN_CURSO, "excursion_actual_regla_determinista")
        self._cargar()
        if self._modelo is None:
            return self._resultado(lectura, CALIDAD_MODELO_NO_DISPONIBLE, "modelo_no_cargado")
        if lectura.time_quality == TIEMPO_NO_SINCRONIZADO:
            return self._resultado(lectura, CALIDAD_HISTORIAL_INSUFICIENTE, "reloj_no_sincronizado")

        previas = [
            h for h in historial
            if h.temperatura_interna is not None
            and math.isfinite(h.temperatura_interna)
            and h.time_quality != TIEMPO_NO_SINCRONIZADO
            and not h.es_manual
        ]
        ts = np.array([_a_ns(h.timestamp) for h in previas] + [_a_ns(lectura.timestamp)], dtype=np.int64)
        temps = np.array([h.temperatura_interna for h in previas] + [interna], dtype=float)
        puertas = np.array(
            [np.nan if h.apertura_refrigerador is None else float(h.apertura_refrigerador) for h in previas]
            + [np.nan if lectura.apertura_refrigerador is None else float(lectura.apertura_refrigerador)]
        )
        try:
            vector, calidad = features_en_instante(
                ts, temps, puertas, _a_ns(lectura.timestamp), self._config, self._manifiesto["conjunto_features"]
            )
            if vector is None:
                return self._resultado(lectura, calidad, "ventana_15min_sin_cobertura_suficiente")
            puntuacion = float(self._modelo.predict_proba(vector.reshape(1, -1))[0, 1])
        except Exception as exc:  # defensa en profundidad, igual que RandomForestRiesgoService
            logger.exception("Fallo en la inferencia preventiva.")
            return self._resultado(lectura, CALIDAD_FALLO_INFERENCIA, f"excepcion:{type(exc).__name__}")
        umbral = float(self._manifiesto["umbral_decision"])
        return self._resultado(
            lectura,
            CALIDAD_OK,
            None,
            clase=NivelRiesgo.RIESGO_PREVENTIVO if puntuacion >= umbral else NivelRiesgo.NORMAL,
            puntuacion=round(puntuacion, 6),
            umbral=umbral,
            vector_features={n: round(float(v), 6) for n, v in zip(self._manifiesto["features"], vector, strict=True)},
        )


_instancia: ServicioPrediccionPreventiva | None = None


def obtener_servicio_preventivo(modo: str, version: str | None, sha256: str | None) -> ServicioPrediccionPreventiva:
    """Una instancia por proceso (el modelo se carga una sola vez). Si la
    configuración cambia, se recrea."""
    global _instancia
    clave = (modo, version or None, sha256 or None)
    if _instancia is None or (_instancia.modo, _instancia._version, _instancia._sha256) != clave:
        _instancia = ServicioPrediccionPreventiva(modo, version, sha256)
    return _instancia
