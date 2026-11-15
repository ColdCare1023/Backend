"""Modelo preventivo de prueba en un registro temporal (pruebas de gobierno de
la IA: activación, desactivación y rechazo al cargar)."""

import numpy as np
import sklearn
from sklearn.ensemble import RandomForestClassifier

from src.infrastructure.ai.prediccion_preventiva import registro
from src.infrastructure.ai.prediccion_preventiva.configuracion import (
    ESTADO_APROBADO,
    ESTADO_VALIDADO_OFFLINE,
    ConfiguracionPreventiva,
)
from src.infrastructure.ai.prediccion_preventiva.features import nombres_features

CONFIG = ConfiguracionPreventiva()


def registrar_preventivo(raiz, version: str, *, sklearn_manifiesto: str | None = None, aprobar: bool = True) -> str:
    """Registra con la CLI (registrar + estado, que anotan historial.json) un
    modelo que da riesgo si la pendiente de 5 min supera 0,05 °C/min.
    Devuelve el SHA-256 del artefacto."""
    nombres = nombres_features(CONFIG)
    rng = np.random.default_rng(0)
    x = rng.normal(0, 1, (300, len(nombres)))
    j = nombres.index("pendiente_5m")
    x[:, j] = rng.uniform(-0.2, 0.2, 300)
    modelo = RandomForestClassifier(n_estimators=10, random_state=0).fit(x, (x[:, j] > 0.05).astype(int))
    registro.registrar(modelo, {
        "version": version, "estado": "EXPERIMENTAL", "conjunto_features": "termico_minimo",
        "features": nombres, "configuracion": CONFIG.como_dict(), "umbral_decision": 0.5,
        "puntuacion_calibrada": False,
        "versiones": {"scikit_learn": sklearn_manifiesto or sklearn.__version__},
    }, raiz)
    if aprobar:
        registro.cambiar_estado(version, ESTADO_VALIDADO_OFFLINE, "prueba", "qa", None, raiz)
        evidencia = raiz / f"exp_d_{version}.json"
        evidencia.write_text("{}", encoding="utf-8")
        registro.cambiar_estado(version, ESTADO_APROBADO, "prueba", "qa", evidencia, raiz)
    return registro.leer_manifiesto(version, raiz)["artefacto"]["sha256"]
