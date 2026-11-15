"""HU-52 y HU-54 (P1-8 de la validación final): a dónde llevan los correos.

- Excursión crítica (y aviso preventivo): a esa alerta, `/alertas?id=<id>`.
- SIN_DATOS y demás incidentes operativos: al panel de alertas (`/alertas`),
  donde está el rail de incidentes y que ven QF, técnico y auditor. Antes
  apuntaban a `/dispositivos?id=`, una pantalla solo del administrador: el QF
  que recibe el correo no podía abrirla."""

import re
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.application.use_cases.generar_alerta import AvisoExcursion
from src.application.use_cases.vigilar_sin_datos import AvisoSinDatos
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.notifications.notificacion_service import (
    componer_correo_excursion,
    componer_correo_preventivo,
    componer_correo_sin_datos,
)

FRONTEND = "https://coldcare.vercel.app"
CAPTURA = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)
ENLACE = re.compile(r"https?://\S+")


def _enlaces(texto: str) -> list[str]:
    return [e.rstrip('".,)') for e in ENLACE.findall(texto)]


def _hrefs(html: str | None) -> list[str]:
    return re.findall(r'href="([^"]+)"', html or "")


@pytest.mark.parametrize("frontend", [FRONTEND, FRONTEND + "/"])
@pytest.mark.parametrize(
    ("componer", "nivel"),
    [(componer_correo_excursion, NivelRiesgo.EXCURSION_CRITICA), (componer_correo_preventivo, NivelRiesgo.RIESGO_PREVENTIVO)],
)
def test_el_correo_de_una_alerta_lleva_a_esa_alerta(componer, nivel, frontend):
    aviso = AvisoExcursion(alerta_id=uuid4(), device_id="ESP32-01", temperatura=9.4, captured_at=CAPTURA, nivel=nivel)

    correo = componer(aviso, "de@x.pe", "para@x.pe", frontend, {"nombre": "Refrigerador 1"})

    destino = f"{FRONTEND}/alertas?id={aviso.alerta_id}"
    assert _enlaces(correo.cuerpo) == [destino]
    assert destino in _hrefs(correo.html)
    assert "/dispositivos" not in correo.cuerpo + (correo.html or "")


@pytest.mark.parametrize("frontend", [FRONTEND, FRONTEND + "/"])
@pytest.mark.parametrize(
    "tipo", ["sin_datos", "monitoreo_suspendido", "excursion_no_verificada", "sensor_fault", "colision_clave"]
)
def test_los_correos_de_incidentes_llevan_al_panel_de_alertas(tipo, frontend):
    aviso = AvisoSinDatos(
        incidente_id=uuid4(), device_id="ESP32-01", abierto_en=CAPTURA, ultimo_reporte_en=CAPTURA,
        umbral_segundos=90, tipo=tipo, detalle={"temperatura_interna": 11.5, "captured_at": CAPTURA.isoformat()},
    )

    correo = componer_correo_sin_datos(aviso, "de@x.pe", "para@x.pe", frontend, {"nombre": "Refrigerador 1"})

    assert _enlaces(correo.cuerpo) == [f"{FRONTEND}/alertas"]
    assert "/dispositivos" not in correo.cuerpo
    assert "Ver el incidente en el panel de alertas" in correo.cuerpo


def test_sin_frontend_url_los_correos_no_inventan_un_enlace():
    aviso = AvisoSinDatos(
        incidente_id=uuid4(), device_id="ESP32-01", abierto_en=CAPTURA, ultimo_reporte_en=CAPTURA, umbral_segundos=90
    )
    assert _enlaces(componer_correo_sin_datos(aviso, "de@x.pe", "para@x.pe").cuerpo) == []
