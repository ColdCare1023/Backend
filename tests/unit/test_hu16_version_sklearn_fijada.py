"""HU-16 (P2 de la re-auditoría): el runtime instala la MISMA versión de
scikit-learn con la que se entrenó el modelo térmico oficial.

El artefacto se entrenó con 1.9.0 y el lockfile resolvía 1.9.1: el pickle
cargaba con InconsistentVersionWarning y sin garantía de predecir igual.
`/health/detalle` lo informaba, pero la versión no estaba fijada."""

import json
import re
from pathlib import Path

import sklearn

from src.infrastructure.ai.random_forest_service import DEFAULT_METADATA_PATH

RAIZ = Path(__file__).resolve().parents[2]


def _fijada(archivo: str) -> str:
    texto = (RAIZ / archivo).read_text(encoding="utf-8")
    coincidencia = re.search(r"^scikit-learn==([0-9][0-9A-Za-z.]*)", texto, flags=re.MULTILINE)
    assert coincidencia, f"{archivo} no fija scikit-learn con =="
    return coincidencia.group(1)


def _entrenado_con() -> str:
    return json.loads(DEFAULT_METADATA_PATH.read_text(encoding="utf-8"))["sklearn_version"]


def test_los_lockfiles_fijan_la_version_de_entrenamiento_del_modelo_oficial():
    entrenado = _entrenado_con()
    for archivo in ("requirements.in", "requirements.txt", "requirements-dev.txt", "requirements-ml.txt"):
        assert _fijada(archivo) == entrenado, archivo


def test_el_entorno_de_pruebas_usa_la_version_fijada():
    # Si falla, el entorno local no se instaló desde los lockfiles.
    assert sklearn.__version__ == _fijada("requirements.txt")
