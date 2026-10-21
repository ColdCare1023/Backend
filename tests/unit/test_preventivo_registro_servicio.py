"""Registro versionado, carga autorizada y servicio de inferencia preventiva."""

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
import sklearn
from sklearn.ensemble import RandomForestClassifier

from src.domain.entities.lectura_termica import ORIGEN_MANUAL, LecturaTermica
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
    ConfiguracionPreventiva,
)
from src.infrastructure.ai.prediccion_preventiva.features import nombres_features
from src.infrastructure.ai.prediccion_preventiva.servicio import (
    MODO_ACTIVO,
    MODO_SOMBRA,
    ServicioPrediccionPreventiva,
)

CONFIG = ConfiguracionPreventiva()
NOMBRES = nombres_features(CONFIG)
VERSION = "rf-prueba-1.0.0"


def _modelo_juguete():
    """Bosque entrenado con vectores sintéticos: riesgo si la pendiente de 5 min > 0,05 °C/min."""
    rng = np.random.default_rng(0)
    x = rng.normal(0, 1, (400, len(NOMBRES)))
    j = NOMBRES.index("pendiente_5m")
    x[:, j] = rng.uniform(-0.2, 0.2, 400)
    y = (x[:, j] > 0.05).astype(int)
    return RandomForestClassifier(n_estimators=20, random_state=0).fit(x, y)


def _manifiesto(estado=ESTADO_EXPERIMENTAL, version=VERSION):
    return {
        "version": version,
        "estado": estado,
        "conjunto_features": "termico_minimo",
        "features": NOMBRES,
        "configuracion": CONFIG.como_dict(),
        "umbral_decision": 0.5,
        "puntuacion_calibrada": False,
        "versiones": {"scikit_learn": sklearn.__version__},
    }


@pytest.fixture
def raiz(tmp_path):
    registro.registrar(_modelo_juguete(), _manifiesto(), tmp_path)
    return tmp_path


# ── Registro ─────────────────────────────────────────────────────


def test_registrar_y_cargar_verificado(raiz):
    modelo, manifiesto = registro.cargar_verificado(VERSION, raiz)
    assert manifiesto["artefacto"]["sha256"] == registro.sha256_archivo(raiz / VERSION / "modelo.joblib")
    assert list(modelo.classes_) == [0, 1]
    historial = json.loads((raiz / "historial.json").read_text(encoding="utf-8"))
    assert historial[0]["evento"] == "registrado"


def test_artefacto_alterado_no_se_deserializa(raiz, monkeypatch):
    ruta = raiz / VERSION / "modelo.joblib"
    ruta.write_bytes(ruta.read_bytes() + b"x")

    def _prohibido(*_a, **_k):
        raise AssertionError("joblib.load no debe llamarse con un hash inválido")

    monkeypatch.setattr(registro.joblib, "load", _prohibido)
    with pytest.raises(registro.ErrorRegistro):
        registro.cargar_verificado(VERSION, raiz)


def test_hash_fijado_en_configuracion_distinto_se_rechaza(raiz):
    with pytest.raises(registro.ErrorRegistro):
        registro.cargar_verificado(VERSION, raiz, sha256_esperado="0" * 64)


def test_no_se_sobrescribe_una_version(raiz):
    with pytest.raises(registro.ErrorRegistro):
        registro.registrar(_modelo_juguete(), _manifiesto(), raiz)


@pytest.mark.parametrize("version", ["../fuera", "a/b", "", "X" * 80])
def test_version_con_ruta_insegura_se_rechaza(raiz, version):
    with pytest.raises(registro.ErrorRegistro):
        registro.directorio_version(version, raiz)


def test_no_se_registra_un_modelo_ya_aprobado(tmp_path):
    with pytest.raises(registro.ErrorRegistro):
        registro.registrar(_modelo_juguete(), _manifiesto(ESTADO_APROBADO), tmp_path)


def test_aprobar_exige_validacion_offline_evidencia_y_responsable(raiz, tmp_path):
    with pytest.raises(registro.ErrorRegistro):
        registro.cambiar_estado(VERSION, ESTADO_APROBADO, "x", "ana", None, raiz)
    registro.cambiar_estado(VERSION, ESTADO_VALIDADO_OFFLINE, "prueba", "ana", None, raiz)
    with pytest.raises(registro.ErrorRegistro):
        registro.cambiar_estado(VERSION, ESTADO_APROBADO, "x", "ana", None, raiz)
    evidencia = tmp_path / "experimento_d.json"
    evidencia.write_text("{}", encoding="utf-8")
    with pytest.raises(registro.ErrorRegistro):
        registro.cambiar_estado(VERSION, ESTADO_APROBADO, "x", "  ", evidencia, raiz)
    m = registro.cambiar_estado(VERSION, ESTADO_APROBADO, "Exp. D aprobado", "ana", evidencia, raiz)
    assert m["estado"] == ESTADO_APROBADO
    assert m["historial_estados"][-1]["evidencia_experimento_d_sha256"]


# ── Servicio ─────────────────────────────────────────────────────

AHORA = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _lectura(t: datetime, temp: float | None, **extra) -> LecturaTermica:
    return LecturaTermica(
        device_id="ESP32-T", timestamp=t, temperatura_ambiental=22.0, humedad_ambiental=50.0,
        temperatura_interna=temp, apertura_refrigerador=None, estado_conectividad="online",
        time_quality="synced", **extra,
    )


def _historial(pendiente: float, minutos: int = 20, paso_s: int = 30) -> list[LecturaTermica]:
    n = minutos * 60 // paso_s
    return [_lectura(AHORA - timedelta(seconds=paso_s * (n - i)), 5.0 + pendiente * (i - n) * paso_s / 60) for i in range(n)]


def test_lectura_estable_es_normal(raiz):
    s = ServicioPrediccionPreventiva(MODO_SOMBRA, VERSION, raiz=raiz)
    p = s.predecir(_lectura(AHORA, 5.0), _historial(0.0))
    assert p.estado == CALIDAD_OK
    assert p.clase == NivelRiesgo.NORMAL
    assert p.vector_features and p.modelo_sha256 and p.horizonte_min == 30


def test_subida_sostenida_es_riesgo_preventivo(raiz):
    s = ServicioPrediccionPreventiva(MODO_SOMBRA, VERSION, raiz=raiz)
    p = s.predecir(_lectura(AHORA, 5.0), _historial(0.12))
    assert p.clase == NivelRiesgo.RIESGO_PREVENTIVO
    assert p.puntuacion >= p.umbral


def test_excursion_critica_no_depende_del_modelo(tmp_path):
    s = ServicioPrediccionPreventiva(MODO_SOMBRA, "no-existe", raiz=tmp_path)
    p = s.predecir(_lectura(AHORA, 9.5), _historial(0.0))
    assert p.estado == CALIDAD_EXCURSION_EN_CURSO and p.clase is None


def test_sensor_fault(raiz):
    p = ServicioPrediccionPreventiva(MODO_SOMBRA, VERSION, raiz=raiz).predecir(_lectura(AHORA, None), [])
    assert p.estado == CALIDAD_SENSOR_FAULT and p.puntuacion is None


def test_historial_insuficiente(raiz):
    p = ServicioPrediccionPreventiva(MODO_SOMBRA, VERSION, raiz=raiz).predecir(_lectura(AHORA, 5.0), _historial(0.0, minutos=3))
    assert p.estado == CALIDAD_HISTORIAL_INSUFICIENTE and p.clase is None


def test_reloj_no_sincronizado_no_se_predice(raiz):
    lectura = _lectura(AHORA, 5.0)
    lectura.time_quality = "unsynced"
    p = ServicioPrediccionPreventiva(MODO_SOMBRA, VERSION, raiz=raiz).predecir(lectura, _historial(0.0))
    assert p.estado == CALIDAD_HISTORIAL_INSUFICIENTE


def test_lecturas_manuales_del_historial_se_ignoran(raiz):
    historial = [_lectura(h.timestamp, 5.0, origen=ORIGEN_MANUAL) for h in _historial(0.0)]
    p = ServicioPrediccionPreventiva(MODO_SOMBRA, VERSION, raiz=raiz).predecir(_lectura(AHORA, 5.0), historial)
    assert p.estado == CALIDAD_HISTORIAL_INSUFICIENTE


def test_modelo_no_disponible_sin_version(tmp_path):
    s = ServicioPrediccionPreventiva(MODO_SOMBRA, "", raiz=tmp_path)
    assert not s.disponible
    assert s.predecir(_lectura(AHORA, 5.0), _historial(0.0)).estado == CALIDAD_MODELO_NO_DISPONIBLE


def test_modelo_incompatible_se_rechaza(raiz):
    ruta = raiz / VERSION / "manifiesto.json"
    m = json.loads(ruta.read_text(encoding="utf-8"))
    m["versiones"]["scikit_learn"] = "0.24.2"
    ruta.write_text(json.dumps(m), encoding="utf-8")
    s = ServicioPrediccionPreventiva(MODO_SOMBRA, VERSION, raiz=raiz)
    assert not s.disponible
    assert "scikit-learn" in s.estado()["motivo"]


def test_features_distintas_se_rechazan(raiz):
    ruta = raiz / VERSION / "manifiesto.json"
    m = json.loads(ruta.read_text(encoding="utf-8"))
    m["features"] = m["features"][::-1]
    ruta.write_text(json.dumps(m), encoding="utf-8")
    assert not ServicioPrediccionPreventiva(MODO_SOMBRA, VERSION, raiz=raiz).disponible


def test_modo_activo_rechaza_un_modelo_experimental(raiz):
    s = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, raiz=raiz)
    assert not s.disponible
    assert s.predecir(_lectura(AHORA, 5.0), _historial(0.0)).estado == CALIDAD_MODELO_NO_DISPONIBLE


def test_fallo_de_inferencia_es_controlado(raiz):
    s = ServicioPrediccionPreventiva(MODO_SOMBRA, VERSION, raiz=raiz)
    assert s.disponible

    class _Roto:
        def predict_proba(self, _x):
            raise ValueError("boom")

    s._modelo = _Roto()
    p = s.predecir(_lectura(AHORA, 5.0), _historial(0.0))
    assert p.estado == CALIDAD_FALLO_INFERENCIA and p.clase is None
