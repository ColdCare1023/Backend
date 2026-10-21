"""BE-013: las vistas de dispositivos para roles operativos no incluyen datos
de contacto del responsable ni el estado de credenciales."""

from datetime import date

from tests.conftest import auth_header

SENSIBLES = {"responsable_nombre", "responsable_email", "responsable_telefono", "mqtt_token_activo",
             "mqtt_credencial_actualizada_en", "instalado_por", "observaciones_instalacion"}


def _preparar(client, token_admin, token_farmaceutico) -> None:
    client.post("/api/dispositivos", json={"device_id": "FARM-BE013"}, headers=auth_header(token_admin))
    client.patch(
        "/api/dispositivos/FARM-BE013/responsable",
        json={"nombre": "Responsable", "email": "responsable@farmacia.example.org", "telefono": "+51999888777"},
        headers=auth_header(token_admin),
    )
    client.patch(
        "/api/dispositivos/FARM-BE013/calibracion",
        json={"fecha_calibracion": date.today().replace(year=date.today().year - 1).isoformat(),
              "numero_certificado": "CERT-1", "meses_vigencia": 12},
        headers=auth_header(token_farmaceutico),
    )


def test_estado_operativo_no_expone_datos_del_responsable(client, token_admin, token_farmaceutico, token_tecnico):
    _preparar(client, token_admin, token_farmaceutico)
    equipos = client.get(
        "/api/dispositivos/estado-operativo", params={"todos": True}, headers=auth_header(token_tecnico)
    ).json()

    equipo = next(e for e in equipos if e["id"] == "FARM-BE013")
    assert not SENSIBLES & equipo.keys()
    assert equipo["estado_operativo"] == "SIN_DATOS"


def test_estado_de_calibracion_no_expone_datos_del_responsable(client, token_admin, token_farmaceutico):
    _preparar(client, token_admin, token_farmaceutico)
    estado = client.get(
        "/api/dispositivos/calibracion/estado", params={"dias_preaviso": 365}, headers=auth_header(token_farmaceutico)
    ).json()

    dispositivos = estado["vencidos"] + estado["proximos_a_vencer"]
    assert any(d["id"] == "FARM-BE013" for d in dispositivos)
    assert all(not SENSIBLES & d.keys() for d in dispositivos)


def test_la_administracion_conserva_la_vista_completa(client, token_admin, token_farmaceutico):
    _preparar(client, token_admin, token_farmaceutico)
    dispositivo = next(d for d in client.get("/api/dispositivos", headers=auth_header(token_admin)).json()
                       if d["id"] == "FARM-BE013")
    assert dispositivo["responsable_email"] == "responsable@farmacia.example.org"
