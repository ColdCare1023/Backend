"""RF-13: el reporte BPA debe declarar cuándo está incompleto.

Hallazgo S-08. El tope de 10.000 registros por colección existe por memoria
(medido: ~81 MB de pico y 12,6 MB de cuerpo JSON para un reporte lleno, sobre
una instancia de 512 MB). El problema no era el tope, sino que se aplicaba en
silencio: a la cadencia de muestreo del firmware —30 s, o 2.880 lecturas al
día— cubre unos 3,5 días, así que un reporte mensual omitía cerca del 88 % del
periodo sin decirlo en ninguna parte.

Las pruebas rebajan el tope en vez de insertar 10.001 lecturas: lo que se
comprueba es la lógica de detección y su propagación hasta el documento, no la
constante.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.application.use_cases import exportar_reporte_bpa
from src.application.use_cases.exportar_reporte_bpa import (
    LIMITE_REGISTROS_REPORTE,
    listar_detectando_truncamiento,
)
from tests.conftest import auth_header

DEVICE = "FARM-TRUNC-01"


@pytest.fixture
def limite_bajo(monkeypatch):
    """Tope de 3 registros: hace la prueba rápida sin cambiar la lógica."""
    monkeypatch.setattr(exportar_reporte_bpa, "LIMITE_REGISTROS_REPORTE", 3)
    return 3


def _ingestar(client, token_tecnico, segundo: int, temperatura: float = 5.0):
    base = datetime.now(tz=timezone.utc).replace(microsecond=0, second=0, minute=0)
    return client.post(
        "/api/lecturas",
        json={
            "device_id": DEVICE,
            "timestamp": (base + timedelta(seconds=segundo)).isoformat(),
            "temperatura_interna": temperatura,
            "temperatura_ambiental": temperatura + 0.5,
            "humedad_ambiental": 60.0,
            "apertura_refrigerador": False,
            "estado_conectividad": "online",
        },
        headers=auth_header(token_tecnico),
    )


# ── Detección ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_detecta_truncamiento_pidiendo_un_registro_de_mas():
    """Se pide `limite + 1`: si vuelve el extra, hay más datos de los que caben.
    Es mucho más barato que un COUNT sobre toda la serie temporal."""
    llamadas = {}

    async def consulta_falsa(*, limite, **kwargs):
        llamadas["limite"] = limite
        return list(range(limite))  # devuelve el registro de más

    registros, truncado = await listar_detectando_truncamiento(consulta_falsa, limite=10)

    assert llamadas["limite"] == 11, "debe pedir uno más que el tope"
    assert truncado is True
    assert len(registros) == 10, "el registro extra no debe aparecer en el reporte"


@pytest.mark.asyncio
async def test_no_marca_truncamiento_cuando_todo_cabe():
    async def consulta_falsa(*, limite, **kwargs):
        return list(range(5))

    registros, truncado = await listar_detectando_truncamiento(consulta_falsa, limite=10)

    assert truncado is False
    assert len(registros) == 5


@pytest.mark.asyncio
async def test_el_caso_frontera_exacto_no_se_declara_truncado():
    """Justo `limite` registros es un reporte completo, no uno recortado."""

    async def consulta_falsa(*, limite, **kwargs):
        return list(range(limite - 1))  # limite+1 pedidos, devuelve limite

    registros, truncado = await listar_detectando_truncamiento(consulta_falsa, limite=10)

    assert truncado is False
    assert len(registros) == 10


# ── Propagación a la API ──────────────────────────────────────────────────


async def test_el_reporte_json_declara_que_esta_truncado(
    client, token_tecnico, token_farmaceutico, limite_bajo
):
    for i in range(limite_bajo + 2):
        _ingestar(client, token_tecnico, segundo=i)

    ahora = datetime.now(tz=timezone.utc)
    body = client.get(
        "/api/reportes/bpa",
        params={
            "fecha_desde": (ahora - timedelta(days=1)).isoformat(),
            "fecha_hasta": (ahora + timedelta(days=1)).isoformat(),
            "device_id": DEVICE,
        },
        headers=auth_header(token_farmaceutico),
    ).json()

    assert body["truncado"] is True
    assert body["lecturas_truncadas"] is True
    assert len(body["lecturas"]) == limite_bajo
    assert body["limite_por_coleccion"] == limite_bajo


async def test_un_reporte_completo_no_se_declara_truncado(
    client, token_tecnico, token_farmaceutico
):
    """Contraprueba: la bandera no debe estar siempre encendida."""
    _ingestar(client, token_tecnico, segundo=1)

    ahora = datetime.now(tz=timezone.utc)
    body = client.get(
        "/api/reportes/bpa",
        params={
            "fecha_desde": (ahora - timedelta(days=1)).isoformat(),
            "fecha_hasta": (ahora + timedelta(days=1)).isoformat(),
            "device_id": DEVICE,
        },
        headers=auth_header(token_farmaceutico),
    ).json()

    assert body["truncado"] is False
    assert body["limite_por_coleccion"] == LIMITE_REGISTROS_REPORTE


# ── Propagación al PDF ────────────────────────────────────────────────────
#
# Se comprueba sobre el generador y no extrayendo el texto del PDF: leerlo
# exigiría añadir pypdf como dependencia de pruebas solo para esto, y lo que
# hay que fijar es que el aviso se compone cuando corresponde y no cuando no.


def test_el_generador_compone_el_aviso_cuando_el_reporte_esta_recortado():
    """El PDF es el entregable formal ante una inspección. Si el aviso solo
    viajara en el JSON, el documento impreso seguiría siendo engañoso."""
    from src.infrastructure.pdf.generador_pdf import GeneradorReporteBPAPDF

    bloques = GeneradorReporteBPAPDF()._aviso_truncamiento(
        {
            "truncado": True,
            "lecturas_truncadas": True,
            "alertas_truncadas": False,
            "trazabilidad_truncada": False,
            "limite_por_coleccion": 10_000,
        }
    )

    assert bloques, "un reporte recortado debe llevar aviso en el documento"


def test_el_generador_no_compone_aviso_si_el_reporte_esta_completo():
    from src.infrastructure.pdf.generador_pdf import GeneradorReporteBPAPDF

    generador = GeneradorReporteBPAPDF()

    assert generador._aviso_truncamiento(None) == []
    assert generador._aviso_truncamiento({"truncado": False}) == []


async def test_el_pdf_se_genera_correctamente_con_el_aviso_incluido(
    client, token_tecnico, token_farmaceutico, limite_bajo
):
    """El aviso se maqueta dentro del documento sin romper la composición."""
    for i in range(limite_bajo + 2):
        _ingestar(client, token_tecnico, segundo=i)

    ahora = datetime.now(tz=timezone.utc)
    response = client.get(
        "/api/reportes/bpa/pdf",
        params={
            "fecha_desde": (ahora - timedelta(days=1)).isoformat(),
            "fecha_hasta": (ahora + timedelta(days=1)).isoformat(),
            "device_id": DEVICE,
        },
        headers=auth_header(token_farmaceutico),
    )

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
