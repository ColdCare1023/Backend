"""QA & Frontend Validation Test Suite for HU-31: Thermal Trend Visualization.

Checklist items covered:
  - [FE] Verificar ventana de 24 h por defecto
  - [FE] Verificar cambio de ventana (6h, 12h, 24h, 7d)
  - [FE] Probar con datos reales (rango objetivo 2–8 °C con franjas visuales)
  - [QA] Registrar evidencia de escenarios (con datos, empty state, gran volumen)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from consumidor_mqtt import app
from grafica_service import generador_grafica_global, MAX_PUNTOS_GRAFICA

EJEMPLOS = Path(__file__).parent.parent / "contrato" / "ejemplos"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_hu31_fe_ventana_de_24_horas_por_defecto(client):
    """[FE] Escenario 4: Si no se especifica ventana, se cargan por defecto las últimas 24 horas."""
    resp = client.get("/api/v1/telemetria/grafica?device_id=ESP32_FARMACIA_01")
    assert resp.status_code == 200
    data = resp.json()

    assert data["device_id"] == "ESP32_FARMACIA_01"
    assert data["ventana_horas"] == 24
    assert "desde" in data
    assert "hasta" in data

    desde = datetime.fromisoformat(data["desde"].replace("Z", "+00:00"))
    hasta = datetime.fromisoformat(data["hasta"].replace("Z", "+00:00"))
    diferencia_horas = (hasta - desde).total_seconds() / 3600.0
    assert abs(diferencia_horas - 24.0) < 0.01


def test_hu31_fe_cambio_de_ventana(client):
    """[FE] Modificación de la ventana de tiempo (6h, 12h, 7 días / 168h)."""
    for horas in [6, 12, 48, 168]:
        resp = client.get(f"/api/v1/telemetria/grafica?device_id=ESP32_FARMACIA_01&horas={horas}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ventana_horas"] == horas
        desde = datetime.fromisoformat(data["desde"].replace("Z", "+00:00"))
        hasta = datetime.fromisoformat(data["hasta"].replace("Z", "+00:00"))
        diferencia = (hasta - desde).total_seconds() / 3600.0
        assert abs(diferencia - float(horas)) < 0.01


def test_hu31_fe_probar_con_datos_reales_y_zonas_2_8(client):
    """[FE] Escenario 1: Telemetría real renderizada con delimitación de zonas 2–8 °C."""
    device_id = "ESP32_FARMACIA_01"
    ahora = datetime.now(timezone.utc)

    # Generar lecturas dentro de la ventana de 24 horas
    lecturas = []
    for m in range(0, 120, 10):  # 12 lecturas
        t_pt = ahora - timedelta(minutes=m)
        lecturas.append({
            "timestamp": t_pt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "temp_ds18b20": 4.50 + (m * 0.02),
            "temp_sht31": 21.0,
            "humedad_sht31": 55.0,
            "door_open": False,
        })

    generador_grafica_global.agregar_lecturas(device_id, lecturas)

    resp = client.get(f"/api/v1/telemetria/grafica?device_id={device_id}&horas=24")
    assert resp.status_code == 200
    data = resp.json()

    assert data["empty_state"] is False
    assert len(data["puntos"]) >= 12

    # Verificación de zonas seguras (2 a 8 °C)
    rango = data["rango_seguro"]
    assert rango["min_objetivo"] == 2.0
    assert rango["max_objetivo"] == 8.0
    zonas = rango["zonas"]
    assert any(z["nombre"] == "rango_seguro" and z["color"] == "#22c55e" for z in zonas)
    assert any(z["nombre"] == "congelacion_riesgo" and z["color"] == "#ef4444" for z in zonas)


def test_hu31_qa_escenario_sin_datos_empty_state(client):
    """[QA] Escenario 2: Dispositivo sin datos muestra estado vacío informativo en vez de lienzo confuso."""
    resp = client.get("/api/v1/telemetria/grafica?device_id=ESP32_NUEVO_SIN_DATOS&horas=24")
    assert resp.status_code == 200
    data = resp.json()

    assert data["empty_state"] is True
    assert data["total_lecturas"] == 0
    assert data["puntos_renderizados"] == 0
    assert len(data["puntos"]) == 0
    assert data["mensaje_empty"] is not None
    assert "Esperando primera telemetría" in data["mensaje_empty"]


def test_hu31_qa_escenario_gran_volumen_downsampling(client):
    """[QA] Escenario 3: Ventana con miles de lecturas aplica downsampling automático sin superar MAX_PUNTOS."""
    device_id = "ESP32_ALTO_VOLUMEN"
    ahora = datetime.now(timezone.utc)

    # Generar 2000 lecturas en una semana
    lecturas = []
    for i in range(2000):
        t_pt = ahora - timedelta(minutes=i * 5)
        lecturas.append({
            "timestamp": t_pt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "temp_ds18b20": 4.0 + ((i % 10) * 0.1),
            "temp_sht31": 20.0,
            "humedad_sht31": 50.0,
            "door_open": False,
        })

    generador_grafica_global.agregar_lecturas(device_id, lecturas)

    resp = client.get(f"/api/v1/telemetria/grafica?device_id={device_id}&horas=168")
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_lecturas"] == 2000
    assert data["downsampled"] is True
    # La cantidad de puntos renderizados no supera el umbral para mantener fluidez
    assert data["puntos_renderizados"] <= MAX_PUNTOS_GRAFICA
    assert len(data["puntos"]) <= MAX_PUNTOS_GRAFICA
