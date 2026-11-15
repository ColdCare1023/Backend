"""HU-18 P-3 (IA-A09): el umbral de decisión se elige con puntuaciones fuera de
la muestra de entrenamiento, separadas por corrida.

Antes, `corridas.evaluar_conjunto` lo elegía con `predict_proba` del propio
modelo sobre sus filas de entrenamiento (`corridas.py:94`), y si eso no daba
umbral usaba 0,5 en silencio. Ahora:

- con `corridas_validacion` (partición de validación), esas corridas no
  ajustan el modelo y solo ellas eligen el umbral;
- sin ellas (pocas corridas), cada corrida de entrenamiento la puntúa un
  modelo ajustado sin ella (dejar una corrida fuera);
- sin puntuaciones fuera de muestra con ambas clases, el conjunto queda "no
  estimable" (no se inventa 0,5)."""

import numpy as np
import pytest
from sklearn.ensemble import RandomForestClassifier

from src.infrastructure.ai.prediccion_preventiva import corridas
from src.infrastructure.ai.prediccion_preventiva.configuracion import ConfiguracionPreventiva
from src.infrastructure.ai.prediccion_preventiva.corridas import (
    METODO_UMBRAL_DEJAR_UNA_FUERA,
    METODO_UMBRAL_PARTICION,
    ejecutar_pipeline_corridas,
)
from src.infrastructure.ai.prediccion_preventiva.entrenamiento import SEMILLA
from tests.corridas_sinteticas import escribir_corridas

CONFIG = ConfiguracionPreventiva(horizonte_min=30)


class _Llamadas:
    """Qué filas vio cada modelo al ajustarse y cuáles puntuó después."""

    def __init__(self) -> None:
        self.ajustadas: dict[int, set[bytes]] = {}
        self.puntuadas: dict[int, set[bytes]] = {}
        # Referencias vivas: sin ellas, un modelo de pliegue liberado deja su
        # id() a otro y se mezclarían las filas de los dos.
        self.modelos: list = []


def _espiar_modelos(monkeypatch) -> _Llamadas:
    llamadas = _Llamadas()

    class ModeloEspia(RandomForestClassifier):
        def fit(self, X, y, sample_weight=None):
            llamadas.modelos.append(self)
            llamadas.ajustadas.setdefault(id(self), set()).update(
                fila.tobytes(order="C") for fila in np.asarray(X, dtype=float)
            )
            return super().fit(X, y, sample_weight)

        def predict_proba(self, X):
            llamadas.puntuadas.setdefault(id(self), set()).update(
                fila.tobytes(order="C") for fila in np.asarray(X, dtype=float)
            )
            return super().predict_proba(X)

    def crear(parametros, semilla=SEMILLA, n_jobs=-1):
        # Menos árboles que en producción: solo importa qué filas ve cada modelo.
        return ModeloEspia(n_estimators=20, max_samples=0.5, **parametros, random_state=semilla, n_jobs=n_jobs)

    monkeypatch.setattr(corridas, "crear_modelo", crear)
    return llamadas


def _espiar_umbrales(monkeypatch) -> list[np.ndarray]:
    """Las etiquetas con las que se elige cada umbral (Random Forest y regla de pendiente)."""
    vistas: list[np.ndarray] = []
    original = corridas.elegir_umbral_f1

    def espia(y, puntuacion):
        vistas.append(np.asarray(y).copy())
        return original(y, puntuacion)

    monkeypatch.setattr(corridas, "elegir_umbral_f1", espia)
    return vistas


@pytest.mark.parametrize("validacion", [None, ["C3"]], ids=["dejar_una_fuera", "particion_validacion"])
def test_ningun_modelo_puntua_las_filas_con_las_que_se_ajusto(tmp_path, monkeypatch, validacion):
    csv = escribir_corridas(tmp_path / "corridas.csv", corridas=6)
    llamadas = _espiar_modelos(monkeypatch)

    informe = ejecutar_pipeline_corridas(csv, ["C4", "C5"], CONFIG, validacion, conjuntos=("termico_minimo",))

    assert informe["resultados"]["termico_minimo"]["resultado"] == "evaluado"
    assert llamadas.puntuadas, "ningún modelo puntuó"
    for modelo, puntuadas in llamadas.puntuadas.items():
        # Antes: el modelo puntuaba sus propias filas de entrenamiento para elegir el umbral.
        assert not puntuadas & llamadas.ajustadas[modelo], "un modelo puntuó filas con las que se ajustó"


def test_con_particion_de_validacion_el_umbral_sale_solo_de_esas_corridas(tmp_path, monkeypatch):
    csv = escribir_corridas(tmp_path / "corridas.csv", corridas=6)
    umbrales = _espiar_umbrales(monkeypatch)

    informe = ejecutar_pipeline_corridas(csv, ["C4", "C5"], CONFIG, ["C3"], conjuntos=("termico_minimo",))

    r = informe["resultados"]["termico_minimo"]
    p, seleccion = r["particiones"], r["seleccion_umbral"]
    assert p["metodo_umbral"] == METODO_UMBRAL_PARTICION
    assert p["validacion"] == ["C3"] and "C3" not in p["entrenamiento"]
    assert p["entrenamiento"] == ["C1", "C2", "C6"] and p["prueba"] == ["C4", "C5"]
    assert seleccion["metodo"] == METODO_UMBRAL_PARTICION
    assert seleccion["observaciones"] == p["observaciones_validacion"] > 0
    assert r["prueba"]["random_forest"]["umbral"] == seleccion["umbral"]
    # Los dos umbrales (RF y regla de pendiente) se eligen con las filas de C3, no con las de ajuste.
    assert umbrales and all(y.size == p["observaciones_validacion"] for y in umbrales)
    assert all(y.size != p["observaciones_entrenamiento"] for y in umbrales)


def test_sin_validacion_cada_corrida_la_puntua_un_modelo_que_no_la_vio(tmp_path):
    csv = escribir_corridas(tmp_path / "corridas.csv", corridas=5)

    informe = ejecutar_pipeline_corridas(csv, ["C4", "C5"], CONFIG, conjuntos=("termico_minimo",))

    seleccion = informe["resultados"]["termico_minimo"]["seleccion_umbral"]
    assert seleccion["metodo"] == METODO_UMBRAL_DEJAR_UNA_FUERA
    assert [p["corrida_validada"] for p in seleccion["pliegues"]] == ["C1", "C2", "C3"]
    for pliegue in seleccion["pliegues"]:
        assert pliegue["puntuada"] is True
        assert pliegue["corrida_validada"] not in pliegue["corridas_ajuste"]
        assert not set(pliegue["corridas_ajuste"]) & {"C4", "C5"}
    assert 0.0 < seleccion["umbral"] < 1.0


def test_sin_puntuaciones_fuera_de_muestra_con_ambas_clases_no_se_usa_05(tmp_path):
    """Solo C1 tiene excursión entre las de entrenamiento: el modelo sin C1 no
    tiene positivos y el que puntúa C2/C3 solo ve negativos. Antes se elegía el
    umbral dentro de la muestra (o 0,5) y el conjunto salía "evaluado"."""
    csv = escribir_corridas(tmp_path / "corridas.csv", corridas=5, con_excursion={1, 4, 5})

    informe = ejecutar_pipeline_corridas(csv, ["C4", "C5"], CONFIG, conjuntos=("termico_minimo",))

    r = informe["resultados"]["termico_minimo"]
    assert r["resultado"].startswith("no estimable: sin umbral fuera de muestra")
    assert r["seleccion_umbral"]["umbral"] is None
    assert "_modelo" not in r


def test_una_corrida_de_validacion_que_es_de_prueba_falla(tmp_path):
    csv = escribir_corridas(tmp_path / "corridas.csv", corridas=5)

    with pytest.raises(ValueError, match="validación que no son de entrenamiento"):
        ejecutar_pipeline_corridas(csv, ["C4", "C5"], CONFIG, ["C4"], conjuntos=("termico_minimo",))


def test_con_una_sola_corrida_de_ajuste_no_hay_umbral_fuera_de_muestra(tmp_path):
    csv = escribir_corridas(tmp_path / "corridas.csv", corridas=3)

    informe = ejecutar_pipeline_corridas(csv, ["C2", "C3"], CONFIG, conjuntos=("termico_minimo",))

    assert "al menos 2 corridas de ajuste" in informe["resultados"]["termico_minimo"]["resultado"]
