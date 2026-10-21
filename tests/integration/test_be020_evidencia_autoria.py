"""BE-020: reglas de autoría y plazo sobre la evidencia de atención.

- Una rectificación sustituye a la justificación vigente: solo la hace su
  autor o el farmacéutico responsable.
- El checklist BPA no se declara retroactivamente (hoy o el día anterior), y
  el PDF muestra cuándo se registró realmente.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from src.domain.value_objects.rol import Rol
from src.infrastructure.pdf.generador_pdf import _registro_checklist
from tests.conftest import auth_header

DEVICE_ID = "FARM-BE020"


@pytest_asyncio.fixture
async def token_otro_tecnico(crear_usuario, client):
    await crear_usuario("Otro Tecnico", "otro.tecnico@farmacia.example.org", "password123", Rol.TECNICO)
    respuesta = client.post(
        "/api/auth/login", data={"username": "otro.tecnico@farmacia.example.org", "password": "password123"}
    )
    return respuesta.json()["access_token"]


def _accion_de(client, token) -> dict:
    lectura = {
        "device_id": DEVICE_ID,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "temperatura_interna": 15.0,
        "temperatura_ambiental": 21.0,
        "humedad_ambiental": 55.0,
        "apertura_refrigerador": False,
        "estado_conectividad": "online",
    }
    assert client.post("/api/lecturas", json=lectura, headers=auth_header(token)).status_code == 201
    alerta = client.get("/api/alertas", params={"device_id": DEVICE_ID}, headers=auth_header(token)).json()[0]
    respuesta = client.post(
        f"/api/alertas/{alerta['id']}/acciones-correctivas",
        json={"descripcion": "Se trasladaron los viales al refrigerador de respaldo."},
        headers=auth_header(token),
    )
    assert respuesta.status_code == 201
    return respuesta.json()


def _rectificar(client, accion_id, token):
    return client.post(
        f"/api/alertas/acciones-correctivas/{accion_id}/rectificar",
        json={"descripcion": "Versión corregida de la justificación."},
        headers=auth_header(token),
    )


# ── Rectificaciones ───────────────────────────────────────────────────────


def test_un_tecnico_no_rectifica_la_accion_de_otro(client, token_tecnico, token_otro_tecnico):
    accion = _accion_de(client, token_tecnico)

    respuesta = _rectificar(client, accion["id"], token_otro_tecnico)

    assert respuesta.status_code == 403
    ciclo = client.get(
        f"/api/alertas/{accion['alert_id']}/acciones-correctivas", headers=auth_header(token_tecnico)
    ).json()
    assert [a["id"] for a in ciclo] == [accion["id"]]


def test_el_autor_rectifica_su_propia_accion(client, token_tecnico):
    accion = _accion_de(client, token_tecnico)

    assert _rectificar(client, accion["id"], token_tecnico).status_code == 201


def test_el_farmaceutico_responsable_rectifica_acciones_ajenas(client, token_tecnico, token_farmaceutico):
    accion = _accion_de(client, token_tecnico)

    respuesta = _rectificar(client, accion["id"], token_farmaceutico)

    assert respuesta.status_code == 201
    assert respuesta.json()["corrige_accion_id"] == accion["id"]


# ── Checklist BPA ─────────────────────────────────────────────────────────


def _checklist(fecha: str) -> dict:
    items = ("temperatura", "termometro", "registros", "alertas_revisadas", "acciones_documentadas",
             "puerta", "limpieza", "exclusivo", "rotulado", "respaldo")
    return {"fecha": fecha, **dict.fromkeys(items, True)}


@pytest.mark.parametrize("dias_atras", [0, 1])
def test_checklist_de_hoy_o_del_dia_anterior_se_admite(client, token_farmaceutico, dias_atras):
    fecha = (datetime.now(tz=timezone.utc).date() - timedelta(days=dias_atras)).isoformat()

    respuesta = client.post("/api/checklist-bpa", json=_checklist(fecha), headers=auth_header(token_farmaceutico))

    assert respuesta.status_code in (200, 201)


@pytest.mark.parametrize("dias_atras", [2, 30])
def test_checklist_retroactivo_se_rechaza(client, token_farmaceutico, dias_atras):
    fecha = (datetime.now(tz=timezone.utc).date() - timedelta(days=dias_atras)).isoformat()

    respuesta = client.post("/api/checklist-bpa", json=_checklist(fecha), headers=auth_header(token_farmaceutico))

    assert respuesta.status_code == 422


def test_el_pdf_muestra_cuando_se_registro_y_modifico_el_checklist():
    registrado = datetime(2026, 10, 21, 13, 5, tzinfo=timezone.utc)

    solo_alta = _registro_checklist({"registrado_en": registrado, "modificado_en": registrado})
    corregido = _registro_checklist(
        {"registrado_en": registrado, "modificado_en": registrado + timedelta(hours=26)}
    )

    assert solo_alta == "2026-10-21 13:05"
    assert corregido == "2026-10-21 13:05<br/>modif. 2026-10-22 15:05"
    assert _registro_checklist({}) == "—"


def test_el_pdf_con_hora_de_registro_se_genera(client, token_farmaceutico):
    hoy = datetime.now(tz=timezone.utc)
    client.post("/api/checklist-bpa", json=_checklist(hoy.date().isoformat()), headers=auth_header(token_farmaceutico))

    respuesta = client.get(
        "/api/reportes/bpa/pdf",
        params={"fecha_desde": (hoy - timedelta(days=1)).isoformat(), "fecha_hasta": (hoy + timedelta(days=1)).isoformat()},
        headers=auth_header(token_farmaceutico),
    )

    assert respuesta.status_code == 200
    assert respuesta.content.startswith(b"%PDF-")
