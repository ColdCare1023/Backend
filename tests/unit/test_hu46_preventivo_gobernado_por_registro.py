"""HU-16 esc. 1 / HU-46 esc. 1-3 (P0-3 de la validación final).

En modo activo el modelo preventivo se gobierna desde el registro de la base:
solo carga si IA_PREVENTIVO_VERSION e IA_PREVENTIVO_SHA256 son los de la
versión ACTIVA registrada. Un manifiesto APPROVED y las variables de entorno no
bastan. Si no coinciden, no se deserializa nada y el motivo queda con un
código estable; nunca lanza.
"""

import numpy as np
import pytest
import sklearn
from sklearn.ensemble import RandomForestClassifier

from src.infrastructure.ai.prediccion_preventiva import registro
from src.infrastructure.ai.prediccion_preventiva import servicio as modulo_servicio
from src.infrastructure.ai.prediccion_preventiva.configuracion import (
    ESTADO_APROBADO,
    ESTADO_VALIDADO_OFFLINE,
    ConfiguracionPreventiva,
)
from src.infrastructure.ai.prediccion_preventiva.features import nombres_features
from src.infrastructure.ai.prediccion_preventiva.servicio import (
    CODIGO_HASH_NO_ES_EL_REGISTRADO,
    CODIGO_SHA256_NO_CONFIGURADO,
    CODIGO_SIN_VERSION_ACTIVA,
    CODIGO_VERSION_NO_ES_LA_ACTIVA,
    MODO_ACTIVO,
    MODO_SOMBRA,
    MOTIVO_ARTEFACTO_DISTINTO,
    ServicioPrediccionPreventiva,
)

VERSION = "rf-gobierno-1.0.0"
CONFIG = ConfiguracionPreventiva()


def _modelo(semilla: int = 0):
    nombres = nombres_features(CONFIG)
    rng = np.random.default_rng(semilla)
    x = rng.normal(0, 1, (200, len(nombres)))
    return RandomForestClassifier(n_estimators=5, random_state=semilla).fit(x, (x[:, 0] > 0).astype(int))


def _registrar(raiz, version=VERSION, aprobar=True, semilla: int = 0) -> str:
    registro.registrar(_modelo(semilla), {
        "version": version, "estado": "EXPERIMENTAL", "conjunto_features": "termico_minimo",
        "features": nombres_features(CONFIG), "configuracion": CONFIG.como_dict(), "umbral_decision": 0.5,
        "puntuacion_calibrada": False, "versiones": {"scikit_learn": sklearn.__version__},
    }, raiz)
    if aprobar:
        registro.cambiar_estado(version, ESTADO_VALIDADO_OFFLINE, "prueba", "qa", None, raiz)
        evidencia = raiz / f"exp_d_{version}.json"
        evidencia.write_text("{}", encoding="utf-8")
        registro.cambiar_estado(version, ESTADO_APROBADO, "prueba", "qa", evidencia, raiz)
    return registro.leer_manifiesto(version, raiz)["artefacto"]["sha256"]


@pytest.fixture
def cargas(monkeypatch):
    llamadas = []
    original = registro.joblib.load

    def _load(ruta, *args, **kwargs):
        llamadas.append(ruta)
        return original(ruta, *args, **kwargs)

    monkeypatch.setattr(registro.joblib, "load", _load)
    return llamadas


def test_aprobado_por_manifiesto_y_entorno_no_basta_sin_version_activa(tmp_path, cargas):
    sha = _registrar(tmp_path)
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=tmp_path)

    assert servicio.disponible is False
    assert cargas == [], "sin versión activa registrada no se deserializa el artefacto"
    assert servicio.motivo_codigo() == CODIGO_SIN_VERSION_ACTIVA
    assert servicio.motivo_rechazo()


def test_version_activa_distinta_no_carga(tmp_path, cargas):
    sha = _registrar(tmp_path)
    servicio = ServicioPrediccionPreventiva(
        MODO_ACTIVO, VERSION, sha, raiz=tmp_path, version_activa=("otra-2.0.0", sha)
    )
    assert servicio.disponible is False and cargas == []
    assert servicio.motivo_codigo() == CODIGO_VERSION_NO_ES_LA_ACTIVA


def test_hash_registrado_distinto_no_carga(tmp_path, cargas):
    sha = _registrar(tmp_path)
    servicio = ServicioPrediccionPreventiva(
        MODO_ACTIVO, VERSION, sha, raiz=tmp_path, version_activa=(VERSION, "f" * 64)
    )
    assert servicio.disponible is False and cargas == []
    assert servicio.motivo_codigo() == CODIGO_HASH_NO_ES_EL_REGISTRADO


def test_sin_sha256_configurado_no_carga(tmp_path, cargas):
    sha = _registrar(tmp_path)
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, None, raiz=tmp_path, version_activa=(VERSION, sha))
    assert servicio.disponible is False and cargas == []
    assert servicio.motivo_codigo() == CODIGO_SHA256_NO_CONFIGURADO


def test_la_version_activa_registrada_si_carga(tmp_path):
    sha = _registrar(tmp_path)
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=tmp_path, version_activa=(VERSION, sha))

    assert servicio.disponible is True
    assert servicio.motivo_codigo() is None
    assert servicio.es_version_activa_registrada() is True
    estado = servicio.estado()
    assert estado["version_activa_registrada"] == VERSION and estado["es_version_activa_registrada"] is True


def test_fijar_la_version_activa_reevalua_la_carga(tmp_path):
    sha = _registrar(tmp_path)
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=tmp_path)
    assert servicio.disponible is False

    servicio.fijar_version_activa((VERSION, sha))
    assert servicio.disponible is True

    # Se activa otra versión en la base: el modelo cargado deja de servir.
    servicio.fijar_version_activa(("otra-2.0.0", "a" * 64))
    assert servicio.disponible is False
    assert servicio.motivo_codigo() == CODIGO_VERSION_NO_ES_LA_ACTIVA


def test_la_sombra_no_exige_el_registro(tmp_path):
    """En sombra el modelo no decide nada: se admite sin versión activa."""
    _registrar(tmp_path, aprobar=False)
    servicio = ServicioPrediccionPreventiva(MODO_SOMBRA, VERSION, raiz=tmp_path)
    assert servicio.disponible is True
    assert servicio.es_version_activa_registrada() is False


def test_motivos_de_activacion_validan_el_artefacto_que_se_cargara(tmp_path):
    sha = _registrar(tmp_path)
    _registrar(tmp_path, version="rf-gobierno-exp-1.0.0", aprobar=False, semilla=1)
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=tmp_path)

    assert servicio.motivos_activacion(VERSION, sha) == []
    assert MOTIVO_ARTEFACTO_DISTINTO in servicio.motivos_activacion(VERSION, "c" * 64)
    otra = servicio.motivos_activacion("rf-gobierno-exp-1.0.0", servicio.hash_artefacto("rf-gobierno-exp-1.0.0"))
    assert any("IA_PREVENTIVO_VERSION" in m for m in otra)
    assert any("IA_PREVENTIVO_SHA256" in m for m in otra)
    assert any("APPROVED_FOR_DEPLOYMENT" in m for m in otra)


def test_la_instancia_del_proceso_se_recrea_si_cambia_la_configuracion(monkeypatch):
    monkeypatch.setattr(modulo_servicio, "_instancia", None)
    a = modulo_servicio.obtener_servicio_preventivo("desactivado", None, None)
    assert modulo_servicio.obtener_servicio_preventivo("desactivado", None, None) is a
    assert modulo_servicio.obtener_servicio_preventivo("sombra", "v", None) is not a
