"""HU-37: el checklist BPA se persiste en el backend (ya no en localStorage)
y cada guardado deja un eslabón verificable en la cadena SHA-256."""

from datetime import datetime, timedelta, timezone

from tests.conftest import auth_header

HOY = datetime.now(tz=timezone.utc).date().isoformat()

CHECKLIST_COMPLETO = {
    "fecha": HOY,
    "temperatura": True,
    "termometro": True,
    "registros": True,
    "alertas_revisadas": True,
    "acciones_documentadas": True,
    "puerta": True,
    "limpieza": True,
    "exclusivo": True,
    "rotulado": True,
    "respaldo": True,
    "observaciones": "Sin novedades en la verificación diaria.",
}


def test_registrar_checklist_persiste_y_marca_conforme(client, token_farmaceutico):
    respuesta = client.post(
        "/api/checklist-bpa", json=CHECKLIST_COMPLETO, headers=auth_header(token_farmaceutico)
    )
    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["fecha"] == HOY
    assert cuerpo["total_conformes"] == 10
    assert cuerpo["conforme"] is True
    assert cuerpo["observaciones"] == "Sin novedades en la verificación diaria."


def test_checklist_incompleto_no_es_conforme_pero_se_guarda(client, token_farmaceutico):
    payload = {**CHECKLIST_COMPLETO, "puerta": False, "limpieza": False}
    respuesta = client.post(
        "/api/checklist-bpa", json=payload, headers=auth_header(token_farmaceutico)
    )
    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["total_conformes"] == 8
    assert cuerpo["conforme"] is False


def test_get_devuelve_el_checklist_guardado_del_dia(client, token_farmaceutico):
    client.post("/api/checklist-bpa", json=CHECKLIST_COMPLETO, headers=auth_header(token_farmaceutico))

    respuesta = client.get("/api/checklist-bpa", headers=auth_header(token_farmaceutico))
    assert respuesta.status_code == 200
    assert respuesta.json()["fecha"] == HOY


def test_get_sin_checklist_registrado_devuelve_null_no_404(client, token_farmaceutico):
    """"Todavía no verificado hoy" es un estado normal del flujo, no un error."""
    respuesta = client.get("/api/checklist-bpa", headers=auth_header(token_farmaceutico))
    assert respuesta.status_code == 200
    assert respuesta.json() is None


def test_reguardar_el_mismo_dia_actualiza_no_duplica(client, token_farmaceutico):
    client.post("/api/checklist-bpa", json=CHECKLIST_COMPLETO, headers=auth_header(token_farmaceutico))
    correccion = {**CHECKLIST_COMPLETO, "limpieza": False, "observaciones": "Escarcha detectada"}
    segunda = client.post(
        "/api/checklist-bpa", json=correccion, headers=auth_header(token_farmaceutico)
    )
    assert segunda.status_code == 201
    assert segunda.json()["total_conformes"] == 9

    historial = client.get("/api/checklist-bpa/historial", headers=auth_header(token_farmaceutico))
    assert historial.status_code == 200
    # Una sola fila para el día, con el valor corregido.
    del_dia = [c for c in historial.json() if c["fecha"] == HOY]
    assert len(del_dia) == 1
    assert del_dia[0]["observaciones"] == "Escarcha detectada"


def test_cada_guardado_deja_eslabon_en_la_cadena_de_trazabilidad(client, token_farmaceutico, token_admin):
    client.post("/api/checklist-bpa", json=CHECKLIST_COMPLETO, headers=auth_header(token_farmaceutico))
    correccion = {**CHECKLIST_COMPLETO, "puerta": False}
    client.post("/api/checklist-bpa", json=correccion, headers=auth_header(token_farmaceutico))

    registros = client.get("/api/trazabilidad", headers=auth_header(token_admin)).json()
    checklists = [r for r in registros if r["tipo_evento"] == "CHECKLIST_BPA"]
    # Dos guardados => dos eslabones: la corrección no borra la declaración previa.
    assert len(checklists) == 2
    assert any(c["payload"]["correccion_de_registro_previo"] is False for c in checklists)
    assert any(c["payload"]["correccion_de_registro_previo"] is True for c in checklists)

    # La cadena sigue íntegra tras insertar los eslabones del checklist.
    verificacion = client.get("/api/trazabilidad/verificar", headers=auth_header(token_admin))
    assert verificacion.status_code == 200
    assert verificacion.json()["integra"] is True


def test_fecha_futura_rechazada(client, token_farmaceutico):
    manana = (datetime.now(tz=timezone.utc).date() + timedelta(days=1)).isoformat()
    respuesta = client.post(
        "/api/checklist-bpa",
        json={**CHECKLIST_COMPLETO, "fecha": manana},
        headers=auth_header(token_farmaceutico),
    )
    assert respuesta.status_code == 422


def test_item_faltante_rechazado(client, token_farmaceutico):
    incompleto = {k: v for k, v in CHECKLIST_COMPLETO.items() if k != "respaldo"}
    respuesta = client.post(
        "/api/checklist-bpa", json=incompleto, headers=auth_header(token_farmaceutico)
    )
    assert respuesta.status_code == 422


def test_requiere_autenticacion(client):
    assert client.post("/api/checklist-bpa", json=CHECKLIST_COMPLETO).status_code == 401
    assert client.get("/api/checklist-bpa").status_code == 401


def test_tecnico_no_puede_firmar_checklist(client, token_tecnico):
    """El checklist BPA es una declaración del responsable técnico farmacéutico."""
    respuesta = client.post(
        "/api/checklist-bpa", json=CHECKLIST_COMPLETO, headers=auth_header(token_tecnico)
    )
    assert respuesta.status_code == 403


def test_checklist_es_privado_por_usuario(client, token_farmaceutico, token_admin):
    """El administrador no ve el checklist firmado por el farmacéutico en su
    propio endpoint: cada declaración pertenece a quien la firmó."""
    client.post("/api/checklist-bpa", json=CHECKLIST_COMPLETO, headers=auth_header(token_farmaceutico))
    respuesta = client.get("/api/checklist-bpa", headers=auth_header(token_admin))
    assert respuesta.status_code == 200
    assert respuesta.json() is None
