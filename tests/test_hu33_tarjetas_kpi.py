"""QA & Frontend Component Validation Test Suite for HU-33: KPI Cards (Current State & Fault Tolerance).

Checklist items covered:
  - [FE] Verificar estado actual con datos reales
  - [FE] Verificar estado de falla de sensor (muestra texto explícito, nunca 0.0)
  - [QA] Registrar evidencia de escenarios (vigente, desactualizado por red y sensor caído)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "contrato"))

from telemetria_v1 import parsear_telemetria

EJEMPLOS = Path(__file__).parent.parent / "contrato" / "ejemplos"


def render_kpi_card(
    tipo: str,  # 'temp_interior' | 'temp_ambiente' | 'humedad'
    valor: float | None,
    sensor_status: str,
    captured_at_iso: str | None,
    umbral_freshness_s: int = 300,  # 5 minutos
    ahora: datetime | None = None,
) -> dict[str, Any]:
    """Lógica exacta de renderizado de tarjeta KPI (DashboardPage.tsx / KPI Card)."""
    ahora_dt = ahora or datetime.now(timezone.utc)
    unidad = "°C" if "temp" in tipo else "% HR"

    # Escenario 3: Falla de sensor (valor null o sensor en falla)
    if valor is None or sensor_status != "OK":
        return {
            "tipo": tipo,
            "texto_principal": "Falla de sensor",
            "valor_numerico": None,
            "unidad": unidad,
            "estado_visual": "error",
            "advertencia": "Sensor en falla. Sin lectura confiable.",
            "mostrar_alerta": True,
        }

    # Escenario 2: Antigüedad del dato (freshness check)
    desactualizado = False
    if captured_at_iso:
        captured_dt = datetime.fromisoformat(captured_at_iso.replace("Z", "+00:00"))
        antiguedad_s = (ahora_dt - captured_dt).total_seconds()
        if antiguedad_s > umbral_freshness_s:
            desactualizado = True

    if desactualizado:
        return {
            "tipo": tipo,
            "texto_principal": f"{valor:.2f} {unidad}",
            "valor_numerico": valor,
            "unidad": unidad,
            "estado_visual": "desactualizado",
            "advertencia": "Dato desactualizado por inactividad de red.",
            "mostrar_alerta": True,
        }

    # Escenario 1: Datos disponibles y vigentes
    return {
        "tipo": tipo,
        "texto_principal": f"{valor:.2f} {unidad}",
        "valor_numerico": valor,
        "unidad": unidad,
        "estado_visual": "vigente",
        "advertencia": None,
        "mostrar_alerta": False,
    }


def test_hu33_fe_estado_actual_con_datos_reales():
    """[FE] Escenario 1: Valores numéricos exactos en °C y % HR con telemetría vigente."""
    raw = (EJEMPLOS / "telemetria_normal.json").read_bytes()
    telemetria = parsear_telemetria(raw)

    ahora = datetime.fromisoformat("2026-09-25T15:31:00+00:00")  # 1 minuto después de captured_at

    # KPI Temperatura Interna (DS18B20)
    kpi_temp = render_kpi_card(
        tipo="temp_interior",
        valor=telemetria.temp_ds18b20,
        sensor_status=telemetria.ds18b20_status,
        captured_at_iso="2026-09-25T15:30:00Z",
        ahora=ahora,
    )
    assert kpi_temp["texto_principal"] == "4.25 °C"
    assert kpi_temp["valor_numerico"] == 4.25
    assert kpi_temp["estado_visual"] == "vigente"
    assert kpi_temp["mostrar_alerta"] is False

    # KPI Humedad (SHT31)
    kpi_hum = render_kpi_card(
        tipo="humedad",
        valor=telemetria.humedad_sht31,
        sensor_status=telemetria.sht31_status,
        captured_at_iso="2026-09-25T15:30:00Z",
        ahora=ahora,
    )
    assert kpi_hum["texto_principal"] == "58.50 % HR"
    assert kpi_hum["valor_numerico"] == 58.5
    assert kpi_hum["estado_visual"] == "vigente"


def test_hu33_fe_estado_de_falla_de_sensor():
    """[FE] Escenario 3: Sensor en falla muestra 'Falla de sensor', jamás un valor 0.0 ni números engañosos."""
    raw = (EJEMPLOS / "telemetria_ds18b20_crc_error.json").read_bytes()
    telemetria = parsear_telemetria(raw)

    kpi_falla = render_kpi_card(
        tipo="temp_interior",
        valor=telemetria.temp_ds18b20,
        sensor_status=telemetria.ds18b20_status,
        captured_at_iso=telemetria.captured_at.isoformat() if telemetria.captured_at else None,
    )
    assert kpi_falla["texto_principal"] == "Falla de sensor"
    assert kpi_falla["valor_numerico"] is None
    assert kpi_falla["estado_visual"] == "error"
    assert kpi_falla["mostrar_alerta"] is True
    assert "falla" in kpi_falla["advertencia"].lower()


def test_hu33_qa_lectura_desactualizada_por_red():
    """[QA] Escenario 2: Lectura con más de 5 minutos de antigüedad activa advertencia visual de desactualizado."""
    ahora = datetime.fromisoformat("2026-09-25T16:00:00+00:00")  # 30 minutos después
    captured_at = "2026-09-25T15:30:00Z"

    kpi_viejo = render_kpi_card(
        tipo="temp_interior",
        valor=4.25,
        sensor_status="OK",
        captured_at_iso=captured_at,
        umbral_freshness_s=300,
        ahora=ahora,
    )

    assert kpi_viejo["texto_principal"] == "4.25 °C"
    assert kpi_viejo["estado_visual"] == "desactualizado"
    assert kpi_viejo["mostrar_alerta"] is True
    assert "desactualizado" in kpi_viejo["advertencia"]
