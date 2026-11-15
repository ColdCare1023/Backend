"""HU-18 P-2 / HU-17: `corridas --registrar` solo registra un conjunto que el
servicio de inferencia sabe servir.

Antes la CLI registraba `--conjunto termico_ambiente` e informaba
`carga_en_sombra: True` (la carga solo compara nombres de features), pero
`servicio.predecir` no le pasa el SHT31 al extractor: con 30 min de historial
en rango y SHT31 válido, toda lectura quedaba HISTORIAL_INSUFICIENTE
(`ventana_15min_sin_cobertura_suficiente`) y el modelo registrado nunca
clasificaba. Ahora, antes de escribir, `comprobar_servicio` carga el modelo en
sombra desde un registro temporal y exige que el servicio clasifique una
lectura de prueba del CSV con el vector y la puntuación del entrenamiento.

Con HU-17 integrado, `termico_ambiente` se registra, clasifica con el SHT31 y,
sin él, la lectura queda sin clasificar con el motivo
`ventana_15min_sin_contexto_ambiental`. El rechazo cuando el servicio no
reproduce el vector del entrenamiento lo cubre
`test_un_vector_distinto_del_entrenamiento_no_se_registra`."""

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.ai.prediccion_preventiva import registro_corridas
from src.infrastructure.ai.prediccion_preventiva.__main__ import main
from src.infrastructure.ai.prediccion_preventiva.servicio import MODO_SOMBRA, ServicioPrediccionPreventiva
from tests.corridas_sinteticas import escribir_corridas

VERSION = "rf-corridas-h30-paridad-0.1.0"
# Contrato entre pistas (HU-17): motivo de omisión cuando falta el SHT31.
MOTIVO_SIN_CONTEXTO_AMBIENTAL = "ventana_15min_sin_contexto_ambiental"


def _registrar(csv, raiz, conjunto):
    return main([
        "corridas", "--csv", str(csv), "--prueba", "C4,C5", "--registrar", "--version", VERSION,
        "--conjunto", conjunto, "--directorio-registro", str(raiz),
    ])


@pytest.fixture
def csv(tmp_path):
    return escribir_corridas(tmp_path / "corridas.csv", corridas=5)


@pytest.mark.parametrize("conjunto", ["termico_minimo", "termico_puerta"])
def test_registrar_comprueba_que_el_servicio_clasifica_con_el_vector_del_entrenamiento(csv, tmp_path, capsys, conjunto):
    assert _registrar(csv, tmp_path / "registro", conjunto) == 0

    paridad = json.loads(capsys.readouterr().out)["paridad_servicio"]
    assert paridad["comprobada"] is True and paridad["estado_servicio"] == "ok"
    assert paridad["corrida"] in ("C4", "C5")
    assert paridad["features"] == {"termico_minimo": 28, "termico_puerta": 30}[conjunto]
    assert paridad["diferencia_max"] <= paridad["tolerancia"] == registro_corridas.TOLERANCIA_PARIDAD


def test_un_vector_distinto_del_entrenamiento_no_se_registra(csv, tmp_path, capsys, monkeypatch):
    """Si el servicio calculara otro vector que el entrenamiento (aquí se
    desplaza la referencia del entrenamiento), no se registra nada."""
    original = registro_corridas.features_de_rejillas

    def desplazado(*args, **kwargs):
        x, ok = original(*args, **kwargs)
        return x + np.where(np.arange(x.shape[1]) == 0, 1e-3, 0.0), ok

    monkeypatch.setattr(registro_corridas, "features_de_rejillas", desplazado)
    raiz = tmp_path / "registro"

    assert _registrar(csv, raiz, "termico_minimo") == 2

    error = capsys.readouterr().err
    assert error.startswith("No se registra el modelo: el conjunto termico_minimo no se puede servir: ")
    assert "el servicio de inferencia calcula temp_actual" in error
    assert not raiz.exists()


def _historial(t0: datetime, minutos: int, *, con_sht31: bool) -> list[LecturaTermica]:
    return [
        LecturaTermica(
            device_id="ESP32_BANCO_01", timestamp=t0 + timedelta(seconds=30 * i),
            temperatura_ambiental=22.0 if con_sht31 else None, humedad_ambiental=55.0 if con_sht31 else None,
            temperatura_interna=5.0, apertura_refrigerador=False, estado_conectividad="online", time_quality="synced",
        )
        for i in range(2 * minutos + 1)
    ]


def test_con_hu17_termico_ambiente_se_registra_clasifica_con_sht31_y_sin_el_da_el_motivo_de_contexto(
    csv, tmp_path, capsys
):
    raiz = tmp_path / "registro"
    assert _registrar(csv, raiz, "termico_ambiente") == 0
    resumen = json.loads(capsys.readouterr().out)
    assert resumen["paridad_servicio"]["comprobada"] is True and resumen["paridad_servicio"]["features"] == 32
    servicio = ServicioPrediccionPreventiva(MODO_SOMBRA, VERSION, resumen["sha256"], raiz=raiz)
    t0 = datetime(2026, 11, 15, 8, 0, tzinfo=timezone.utc)

    *previas, actual = _historial(t0, 30, con_sht31=True)
    con = servicio.predecir(actual, previas)
    assert con.estado == "ok", con.motivo
    assert con.clase in (NivelRiesgo.NORMAL, NivelRiesgo.RIESGO_PREVENTIVO)
    assert con.vector_features["temp_amb_actual"] == 22.0 and con.vector_features["humedad_actual"] == 55.0

    *previas, actual = _historial(t0, 30, con_sht31=False)
    sin = servicio.predecir(actual, previas)
    assert (sin.estado, sin.motivo, sin.clase) == ("HISTORIAL_INSUFICIENTE", MOTIVO_SIN_CONTEXTO_AMBIENTAL, None)
