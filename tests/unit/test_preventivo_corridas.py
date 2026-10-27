"""HU-17/HU-18: pipeline formal con corridas controladas (TI §1.4.1).

Se generan corridas sintéticas con la forma de las de banco (excursión
provocada) solo para comprobar el MÉTODO: exclusiones contadas, partición por
corrida con separación ≥ H, baselines, los dos juegos de criterios y la
ablación con y sin SHT31. Las cifras de desempeño reales salen de las
corridas en hardware, no de esta prueba."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from src.infrastructure.ai.prediccion_preventiva.configuracion import ConfiguracionPreventiva
from src.infrastructure.ai.prediccion_preventiva.corridas import ejecutar_pipeline_corridas
from src.infrastructure.ai.prediccion_preventiva.particion import dividir_por_corrida

INICIO = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)


def _corridas_csv(ruta, separacion_h: float = 2.0, corridas: int = 5):
    rng = np.random.default_rng(7)
    filas = []
    for c in range(corridas):
        t0 = INICIO + timedelta(hours=c * (4 + separacion_h))
        for i in range(4 * 120):  # 4 h cada 30 s
            minuto = i / 2
            temp = 5.0 + rng.normal(0, 0.05)
            if 150 <= minuto < 200:
                temp += (minuto - 150) * 0.12  # sube hasta ~11 °C
            elif 200 <= minuto < 220:
                temp += max(0.0, 6.0 - (minuto - 200) * 0.3)
            filas.append({
                "device_id": "FARM-BANCO-01", "corrida_id": f"C{c + 1}",
                "timestamp": (t0 + timedelta(seconds=30 * i)).isoformat(),
                "temperatura_interna": round(temp, 3),
                "temperatura_ambiental": round(22.0 + 0.02 * max(0.0, minuto - 140), 2),
                "humedad_ambiental": 55.0,
                "ds18b20_status": "OK", "time_quality": "sntp_synced",
            })
    filas[10]["ds18b20_status"] = "SENSOR_FAULT"
    filas[11]["time_quality"] = "unsynced"
    pd.DataFrame(filas).to_csv(ruta, index=False)
    return ruta


@pytest.fixture
def csv(tmp_path):
    return _corridas_csv(tmp_path / "corridas.csv")


def test_el_pipeline_reporta_exclusiones_particiones_baselines_criterios_y_ablacion(csv):
    informe = ejecutar_pipeline_corridas(csv, ["C4", "C5"], ConfiguracionPreventiva(horizonte_min=30))

    assert informe["importacion"]["descartes"]["sensor_fault"] == 1
    assert informe["importacion"]["descartes"]["time_quality_unsynced"] == 1
    assert informe["horizonte_min"] == 30
    for conjunto in ("termico_minimo", "termico_ambiente"):
        r = informe["resultados"][conjunto]
        assert r["resultado"] == "evaluado"
        assert r["particiones"]["prueba"] == ["C4", "C5"]
        assert r["particiones"]["separacion_min"] >= 30
        assert set(r["prueba"]) == {"random_forest", "dummy_prior", "regla_pendiente"}
        assert set(r["criterios"]) == {"f1", "por_incidente"}
        assert "historial_insuficiente" in r["observaciones_excluidas"]
    assert set(informe["ablacion_sht31"]) == {
        "f1_macro", "sensibilidad_incidente", "falsas_alarmas_por_dia_sensor", "ap_random_forest",
    }


def test_una_corrida_a_menos_de_h_de_prueba_sale_de_entrenamiento():
    config = ConfiguracionPreventiva(horizonte_min=30)
    minuto = 60 * 1_000_000_000
    tabla = pd.DataFrame({
        "corrida_id": ["C1"] * 3 + ["C2"] * 3 + ["C3"] * 3,
        "entorno_id": ["e"] * 9,
        # C2 termina 10 min antes de que empiece C3 (prueba): debe descartarse.
        "g_ns": [0, minuto, 2 * minuto, 100 * minuto, 101 * minuto, 102 * minuto,
                 112 * minuto, 113 * minuto, 114 * minuto],
    })

    particion = dividir_por_corrida(tabla, ["C3"], config)

    assert particion.corridas_entrenamiento == ["C1"]
    assert "C2" in particion.corridas_descartadas
    assert particion.separacion_min == pytest.approx(110.0)


def test_sin_corrida_id_la_particion_falla_explicitamente():
    with pytest.raises(ValueError, match="corrida_id"):
        dividir_por_corrida(pd.DataFrame({"g_ns": [0]}), ["C1"], ConfiguracionPreventiva())
