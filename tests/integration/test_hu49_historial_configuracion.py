"""HU-49: historial auditable de configuración del hardware.

Esc. 1: instalación, calibración, intervalo, responsable y reemplazo con valor
anterior, valor nuevo, actor y timestamp. Esc. 2: desde cuándo estuvo vigente
cada cambio, con filtros. Esc. 3: la telemetría previa y sus hashes no se
recalculan. Esc. 4: el rango 2-8 °C no es configurable."""

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from src.domain.value_objects.rango_termico import RANGO_TERMICO_BPA
from tests.conftest import auth_header

DEVICE = "FARM-HU49-01"
NUEVO = "FARM-HU49-02"


def _alta(client, token, device=DEVICE, ubicacion="Estante 1") -> None:
    respuesta = client.post(
        "/api/dispositivos",
        json={"device_id": device, "ubicacion": ubicacion, "sensores_habilitados": ["ds18b20"]},
        headers=auth_header(token),
    )
    assert respuesta.status_code == 201, respuesta.text


def _historial(client, token, device=DEVICE, **params) -> list[dict]:
    respuesta = client.get(
        f"/api/dispositivos/{device}/historial-configuracion", params=params, headers=auth_header(token)
    )
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _admin_id(client, token_admin) -> str:
    return next(u["id"] for u in client.get("/api/usuarios", headers=auth_header(token_admin)).json()
                if u["rol"] == "administrador")


def test_esc1_cada_tipo_de_cambio_con_actor(client, token_admin, token_auditor, token_farmaceutico):
    _alta(client, token_admin)
    _alta(client, token_admin, NUEVO)
    admin = auth_header(token_admin)
    client.patch(f"/api/dispositivos/{DEVICE}/instalacion", json={"ubicacion": "Estante 2"}, headers=admin)
    client.patch(f"/api/dispositivos/{DEVICE}/intervalo", json={"segundos": 20}, headers=admin)
    for certificado in ("C-1", "C-2"):
        respuesta = client.patch(
            f"/api/dispositivos/{DEVICE}/calibracion",
            json={"fecha_calibracion": "2026-08-02", "numero_certificado": certificado, "meses_vigencia": 12},
            headers=auth_header(token_farmaceutico),
        )
        assert respuesta.status_code == 200, respuesta.text
    client.patch(f"/api/dispositivos/{DEVICE}/responsable", json={"nombre": "Q.F. Ana"}, headers=admin)
    client.patch(f"/api/dispositivos/{DEVICE}/responsable", json={"nombre": "Q.F. Bea"}, headers=admin)
    baja = client.post(
        f"/api/dispositivos/{DEVICE}/baja", json={"motivo": "reemplazo", "device_id_reemplazo": NUEVO}, headers=admin
    )
    assert baja.status_code == 200, baja.text
    admin_id = _admin_id(client, token_admin)

    filas = _historial(client, token_auditor)  # el auditor lo consulta
    por_campo: dict[str, list[dict]] = {}
    for fila in filas:
        por_campo.setdefault(fila["campo_base"], []).append(fila)

    # Reemplazo y baja: con actor (antes quedaban sin autor).
    [reemplazo] = por_campo["reemplazado_por"]
    assert reemplazo["actor_id"] == admin_id and reemplazo["valor_nuevo"] == NUEVO
    assert reemplazo["actor_nombre"] == "Admin Test"
    assert reemplazo["categoria"] == "reemplazo"
    [baja_fila] = por_campo["baja"]
    assert baja_fila["valor_nuevo"] == "reemplazo" and baja_fila["actor_id"] == admin_id
    [inverso] = [f for f in _historial(client, token_auditor, NUEVO) if f["campo"] == "reemplaza_a"]
    assert inverso["valor_nuevo"] == DEVICE and inverso["actor_id"] == admin_id
    # Calibración: el certificado también se historiza.
    certificados = [(f["valor_anterior"], f["valor_nuevo"]) for f in por_campo["numero_certificado_calibracion"]]
    assert certificados == [("C-1", "C-2"), (None, "C-1")]  # más reciente primero
    assert {f["categoria"] for f in por_campo["numero_certificado_calibracion"]} == {"calibracion"}
    assert "fecha_proxima_calibracion" in por_campo and "fecha_ultima_calibracion" in por_campo
    # Intervalo: campo base, origen y categoría para filtrar.
    [intervalo] = por_campo["intervalo_reporte_segundos"]
    assert intervalo["campo"] == "intervalo_reporte_segundos (administrador)"
    assert intervalo["origen"] == "administrador"
    assert intervalo["categoria"] == "intervalo"
    assert intervalo["valor_nuevo"] == "20"
    # Responsable: un cambio solo de nombre también queda.
    nombres = [(f["valor_anterior"], f["valor_nuevo"]) for f in por_campo["responsable_nombre"]]
    assert (None, "Q.F. Ana") in nombres and ("Q.F. Ana", "Q.F. Bea") in nombres
    assert all(f["categoria"] == "responsable" for f in por_campo["responsable_nombre"])


def test_esc1_el_alta_registra_la_configuracion_inicial(client, token_admin, token_auditor):
    """P2 (re-auditoría): la ubicación y los sensores del alta constan en el
    historial con su actor; ningún endpoint posterior cambia los sensores, así
    que sin esta fila no había desde cuándo rigen."""
    _alta(client, token_admin)
    sin_ubicacion = client.post(
        "/api/dispositivos", json={"device_id": NUEVO, "sensores_habilitados": []}, headers=auth_header(token_admin)
    )
    assert sin_ubicacion.status_code == 201, sin_ubicacion.text
    admin_id = _admin_id(client, token_admin)

    filas = {f["campo"]: f for f in _historial(client, token_auditor)}
    assert set(filas) == {"ubicacion (alta)", "sensores_habilitados (alta)"}
    sensores = filas["sensores_habilitados (alta)"]
    assert (sensores["valor_anterior"], sensores["valor_nuevo"]) == (None, "ds18b20")
    assert sensores["campo_base"] == "sensores_habilitados" and sensores["categoria"] == "sensores"
    assert sensores["origen"] == "alta" and sensores["actor_id"] == admin_id
    assert sensores["vigente_hasta"] is None
    assert filas["ubicacion (alta)"]["valor_nuevo"] == "Estante 1"
    assert filas["ubicacion (alta)"]["categoria"] == "instalacion"
    # Sin ubicación declarada no se inventa una fila; los sensores sí constan.
    del_nuevo = _historial(client, token_auditor, NUEVO)
    assert [(f["campo"], f["valor_nuevo"]) for f in del_nuevo] == [("sensores_habilitados (alta)", "ninguno")]


def test_esc1_la_baja_queda_encadenada_con_su_actor(client, token_admin, token_auditor):
    _alta(client, token_admin)
    client.post(f"/api/dispositivos/{DEVICE}/baja", json={"motivo": "falla_hardware"}, headers=auth_header(token_admin))

    cadena = client.get(
        "/api/trazabilidad", params={"chain_id": DEVICE, "tipo_evento": "BAJA_HARDWARE"},
        headers=auth_header(token_auditor),
    ).json()

    assert cadena[0]["payload"]["admin_id"] == _admin_id(client, token_admin)
    assert cadena[0]["usuario_id"] == cadena[0]["payload"]["admin_id"]


def test_esc2_desde_cuando_estuvo_vigente_cada_cambio(client, token_admin, token_auditor):
    _alta(client, token_admin)
    admin = auth_header(token_admin)
    client.patch(f"/api/dispositivos/{DEVICE}/instalacion", json={"ubicacion": "Estante 2"}, headers=admin)
    client.patch(f"/api/dispositivos/{DEVICE}/instalacion", json={"ubicacion": "Estante 3"}, headers=admin)
    client.patch(f"/api/dispositivos/{DEVICE}/intervalo", json={"segundos": 25}, headers=admin)

    ubicaciones = _historial(client, token_auditor, campo="ubicacion")

    # La ubicación del alta también consta, con su origen (desde cuándo rige).
    assert [f["valor_nuevo"] for f in ubicaciones] == ["Estante 3", "Estante 2", "Estante 1"]
    actual, previa, inicial = ubicaciones
    assert actual["vigente_hasta"] is None  # sigue vigente
    assert previa["vigente_desde"] == previa["created_at"]
    assert previa["vigente_hasta"] == actual["vigente_desde"]
    assert inicial["origen"] == "alta" and inicial["valor_anterior"] is None
    assert inicial["vigente_hasta"] == previa["vigente_desde"]
    # Filtros por categoría y periodo.
    assert {f["categoria"] for f in _historial(client, token_auditor, categoria="intervalo")} == {"intervalo"}
    futuro = (datetime.now(tz=timezone.utc) + timedelta(days=1)).isoformat()
    assert _historial(client, token_auditor, desde=futuro) == []
    assert len(_historial(client, token_auditor, hasta=futuro)) >= 3


def test_esc2_solo_admin_y_auditor_consultan(client, token_admin, token_tecnico, token_farmaceutico):
    _alta(client, token_admin)
    for token in (token_tecnico, token_farmaceutico):
        respuesta = client.get(f"/api/dispositivos/{DEVICE}/historial-configuracion", headers=auth_header(token))
        assert respuesta.status_code == 403


def test_esc3_la_telemetria_y_sus_hashes_no_se_recalculan(client, token_admin, token_tecnico, token_auditor):
    _alta(client, token_admin)
    for temperatura in (5.0, 6.0):
        respuesta = client.post(
            "/api/lecturas",
            json={"device_id": DEVICE, "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                  "temperatura_interna": temperatura},
            headers=auth_header(token_tecnico),
        )
        assert respuesta.status_code == 201, respuesta.text
    lecturas_antes = client.get("/api/lecturas", params={"device_id": DEVICE}, headers=auth_header(token_auditor)).json()
    cadena_antes = client.get(
        "/api/trazabilidad", params={"chain_id": DEVICE, "limite": 1000}, headers=auth_header(token_auditor)
    ).json()

    admin = auth_header(token_admin)
    client.patch(f"/api/dispositivos/{DEVICE}/instalacion", json={"ubicacion": "Estante 9"}, headers=admin)
    client.patch(f"/api/dispositivos/{DEVICE}/intervalo", json={"segundos": 15}, headers=admin)
    client.patch(f"/api/dispositivos/{DEVICE}/responsable", json={"nombre": "Z", "email": "z@farmacia.example.org"},
                 headers=admin)

    lecturas_despues = client.get(
        "/api/lecturas", params={"device_id": DEVICE}, headers=auth_header(token_auditor)
    ).json()
    cadena_despues = {
        r["id"]: r
        for r in client.get(
            "/api/trazabilidad", params={"chain_id": DEVICE, "limite": 1000}, headers=auth_header(token_auditor)
        ).json()
    }
    assert lecturas_despues == lecturas_antes
    assert all(cadena_despues[r["id"]] == r for r in cadena_antes)
    assert len(cadena_despues) > len(cadena_antes)  # los cambios se anexan, no reescriben
    verificacion = client.get(
        "/api/trazabilidad/verificar", params={"chain_id": DEVICE}, headers=auth_header(token_auditor)
    ).json()
    assert verificacion["estado"] == "integra"


@pytest.mark.parametrize(
    ("metodo", "ruta", "cuerpo", "rol"),
    [
        ("post", "/api/dispositivos", {"device_id": "FARM-HU49-09", "rango_min": 0, "rango_max": 10}, "admin"),
        ("patch", f"/api/dispositivos/{DEVICE}/instalacion", {"ubicacion": "x", "temp_min": 0, "temp_max": 10}, "admin"),
        ("patch", f"/api/dispositivos/{DEVICE}/intervalo", {"segundos": 30, "rango_termico": [0, 10]}, "admin"),
        ("patch", f"/api/dispositivos/{DEVICE}/responsable", {"nombre": "x", "minimo_celsius": 0}, "admin"),
        (
            "patch",
            f"/api/dispositivos/{DEVICE}/calibracion",
            {"fecha_calibracion": "2026-08-01", "numero_certificado": "C", "meses_vigencia": 12, "maximo_celsius": 10},
            "farmaceutico",
        ),
    ],
)
def test_esc4_el_rango_2_8_no_se_puede_configurar(
    client, token_admin, token_farmaceutico, metodo, ruta, cuerpo, rol
):
    _alta(client, token_admin)
    token = token_admin if rol == "admin" else token_farmaceutico

    respuesta = getattr(client, metodo)(ruta, json=cuerpo, headers=auth_header(token))

    assert respuesta.status_code == 422
    assert (RANGO_TERMICO_BPA.minimo_celsius, RANGO_TERMICO_BPA.maximo_celsius) == (2.0, 8.0)
    assert not any(f["campo_base"].startswith(("rango", "temp_", "minimo", "maximo"))
                   for f in _historial(client, token_admin))


def test_esc4_el_rango_es_inmutable_en_el_dominio():
    with pytest.raises(dataclasses.FrozenInstanceError):
        RANGO_TERMICO_BPA.maximo_celsius = 10.0  # type: ignore[misc]
