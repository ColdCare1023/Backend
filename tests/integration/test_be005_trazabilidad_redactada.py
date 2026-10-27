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
    # B-10: el auditor ve el detalle completo, pero el correo ya se escribió
    # enmascarado (la cadena es inmutable; Ley 29733).
    assert eslabon["payload"]["detalle"]["rol"] == NUEVO["rol"]
    assert eslabon["payload"]["detalle"]["email"] == "p***@farmacia.example.org"
    assert NUEVO["email"] not in str(eslabon["payload"])


def test_la_redaccion_no_rompe_la_verificacion(client, token_admin, token_tecnico):
    client.post("/api/usuarios", json=NUEVO, headers=auth_header(token_admin))
    verificacion = client.get("/api/trazabilidad/verificar", headers=auth_header(token_tecnico)).json()
    assert verificacion["integra"] is True


def test_el_contacto_del_responsable_se_minimiza_en_la_cadena_y_el_historial(client, token_admin, token_auditor):
    """B-10: el contacto completo solo vive en `devices` (rectificable)."""
    cabecera = auth_header(token_admin)
    client.post("/api/dispositivos", json={"device_id": "FARM-B10-01"}, headers=cabecera)
    client.patch(
        "/api/dispositivos/FARM-B10-01/responsable",
        json={"nombre": "QF Ana", "email": "ana.quispe@farmacia.example.org", "telefono": "+51987654321"},
        headers=cabecera,
    )

    eslabones = client.get("/api/trazabilidad", headers=auth_header(token_auditor)).json()
    responsable = next(e for e in eslabones if e["tipo_evento"] == "RESPONSABLE_DISPOSITIVO_ACTUALIZADO")
    assert responsable["payload"]["responsable_email"] == "a***@farmacia.example.org"
    assert responsable["payload"]["responsable_telefono"] == "***321"
    historial = client.get("/api/dispositivos/FARM-B10-01/historial-configuracion", headers=cabecera).json()
    assert all("ana.quispe" not in (h["valor_nuevo"] or "") for h in historial)
    dispositivo = next(d for d in client.get("/api/dispositivos", headers=cabecera).json() if d["id"] == "FARM-B10-01")
    assert dispositivo["responsable_email"] == "ana.quispe@farmacia.example.org"
