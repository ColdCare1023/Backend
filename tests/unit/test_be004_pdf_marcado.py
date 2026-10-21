"""BE-004: los datos dinámicos del reporte BPA se insertan como texto, nunca
como marcado de ReportLab (`<a href>`, `<img src>`, `<font>`)."""

from datetime import datetime, timezone

from src.application.use_cases.exportar_reporte_bpa_pdf import _calcular_estadisticas
from src.infrastructure.pdf.generador_pdf import GeneradorReporteBPAPDF

MALICIOSO = '<a href="https://phishing.example">clic</a><img src="file:///etc/hosts"/><font color="white">x</font>'


def _generar(**sobrescribir) -> bytes:
    argumentos = {
        "lecturas": [],
        "alertas": [{"created_at": datetime.now(tz=timezone.utc), "device_id": "D1", "nivel_riesgo": "normal",
                     "mensaje": MALICIOSO, "episodio_abierto": False, "revisada": False}],
        "trazabilidad": [],
        "checklists": [{"fecha": "2026-10-21", "total_conformes": 10, "conforme": True, "observaciones": MALICIOSO}],
        "estadisticas": {**_calcular_estadisticas([]), "alertas_criticas": 0},
        "veredicto_integridad": {"integra": True, "total_registros": 0, "verificado_en": "-", "digest_cadena": "N/A"},
        "fecha_desde": "2026-10-20",
        "fecha_hasta": "2026-10-21",
        "usuario": MALICIOSO,
        "device_id": "D1</b><a href='https://x.example'>y</a>",
    }
    argumentos.update(sobrescribir)
    return GeneradorReporteBPAPDF().generar(**argumentos)


def test_marcado_inyectado_no_produce_enlaces_ni_falla():
    pdf = _generar()

    assert pdf.startswith(b"%PDF-")
    # Un <a href> interpretado genera una anotación de enlace (/URI) en el PDF.
    assert b"/URI" not in pdf
    assert b"phishing.example" not in pdf.split(b"stream")[0]


def test_reporte_rechaza_device_id_con_caracteres_de_marcado(client, token_farmaceutico):
    from tests.conftest import auth_header

    for ruta in ("/api/reportes/bpa", "/api/reportes/bpa/pdf"):
        respuesta = client.get(
            ruta,
            params={"fecha_desde": "2026-10-20T00:00:00Z", "fecha_hasta": "2026-10-21T00:00:00Z",
                    "device_id": "<img src='x'/>"},
            headers=auth_header(token_farmaceutico),
        )
        assert respuesta.status_code == 422, ruta
