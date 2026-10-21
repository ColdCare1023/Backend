"""BE-005: la bitácora administrativa y los datos personales que se encadenan
no se exponen por /api/trazabilidad a roles sin función de auditoría."""

from tests.conftest import auth_header

NUEVO = {"nombre": "Persona Nueva", "email": "persona.nueva@farmacia.example.org",
         "password": "ClaveSegura123", "rol": "tecnico"}


def _eslabones_de_auditoria(client, token) -> list[dict]:
    registros = client.get(
        "/api/trazabilidad", params={"chain_id": "SISTEMA", "tipo_evento": "AUDIT_LOG", "limite": 1000},
        headers=auth_header(token),
    ).json()
    return [r for r in registros if r["payload"].get("accion") == "CREAR_USUARIO"]


def test_tecnico_no_ve_ip_ni_detalle_de_la_bitacora(client, token_admin, token_tecnico):
    assert client.post("/api/usuarios", json=NUEVO, headers=auth_header(token_admin)).status_code == 201

    eslabon = _eslabones_de_auditoria(client, token_tecnico)[0]
    assert eslabon["payload"]["detalle"] == "[redactado]"
    assert eslabon["payload"]["ip_origen"] == "[redactado]"
    assert eslabon["payload"]["accion"] == "CREAR_USUARIO"
    assert NUEVO["email"] not in str(eslabon)


def test_auditor_ve_el_eslabon_completo(client, token_admin, token_auditor):
    client.post("/api/usuarios", json=NUEVO, headers=auth_header(token_admin))

    eslabon = _eslabones_de_auditoria(client, token_auditor)[0]
    assert eslabon["payload"]["detalle"]["email"] == NUEVO["email"]


def test_la_redaccion_no_rompe_la_verificacion(client, token_admin, token_tecnico):
    client.post("/api/usuarios", json=NUEVO, headers=auth_header(token_admin))
    verificacion = client.get("/api/trazabilidad/verificar", headers=auth_header(token_tecnico)).json()
    assert verificacion["integra"] is True
