"""P2 de la validación final del backlog.

- HU-49: el historial de configuración de un `device_id` que no existe es un
  404, no un 200 con `[]` (que el auditor leería como «sin cambios»).
- HU-51: PATCH /instalacion es un PATCH: un campo omitido conserva su valor.
  Antes, omitir `ubicacion` la dejaba en null y quedaba un evento histórico
  «ubicacion: Estante 1 → null» que nadie pidió. Enviar `null` explícito sigue
  borrándola."""

from tests.conftest import auth_header

DEVICE = "FARM-P2-01"


def _alta(client, token_admin) -> None:
    respuesta = client.post(
        "/api/dispositivos",
        json={"device_id": DEVICE, "ubicacion": "Estante 1", "sensores_habilitados": ["ds18b20"]},
        headers=auth_header(token_admin),
    )
    assert respuesta.status_code == 201, respuesta.text


def _instalacion(client, token_admin, cuerpo: dict) -> dict:
    respuesta = client.patch(f"/api/dispositivos/{DEVICE}/instalacion", json=cuerpo, headers=auth_header(token_admin))
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _historial(client, token, device=DEVICE):
    return client.get(f"/api/dispositivos/{device}/historial-configuracion", headers=auth_header(token))


# ── HU-49 ────────────────────────────────────────────────────────────────


def test_hu49_historial_de_un_dispositivo_inexistente_es_404(client, token_auditor, token_admin):
    respuesta = _historial(client, token_auditor, "NO-EXISTE-01")
    assert respuesta.status_code == 404
    assert "NO-EXISTE-01" in respuesta.json()["detail"]
    assert _historial(client, token_admin, "NO-EXISTE-01").status_code == 404


def test_hu49_un_dispositivo_existente_sin_cambios_sigue_siendo_200(client, token_auditor, token_admin):
    _alta(client, token_admin)
    respuesta = _historial(client, token_auditor)
    assert respuesta.status_code == 200
    assert isinstance(respuesta.json(), list)


# ── HU-51 ────────────────────────────────────────────────────────────────


def test_hu51_omitir_la_ubicacion_no_la_borra(client, token_admin, token_auditor):
    _alta(client, token_admin)

    dispositivo = _instalacion(
        client, token_admin, {"fecha_instalacion": "2026-08-01", "observaciones": "Sonda en el estante medio"}
    )

    assert dispositivo["ubicacion"] == "Estante 1"
    assert dispositivo["fecha_instalacion"] == "2026-08-01"
    assert dispositivo["observaciones_instalacion"] == "Sonda en el estante medio"
    # El alta deja su propio evento de ubicación; el PATCH no añade ninguno.
    ubicaciones = [
        (h["valor_anterior"], h["valor_nuevo"])
        for h in _historial(client, token_auditor).json() if h["campo_base"] == "ubicacion"
    ]
    assert ("Estante 1", None) not in ubicaciones
    assert all(nuevo == "Estante 1" for _, nuevo in ubicaciones)


def test_hu51_omitir_fecha_y_observaciones_tampoco_las_borra(client, token_admin, token_auditor):
    _alta(client, token_admin)
    _instalacion(client, token_admin, {"fecha_instalacion": "2026-08-01", "observaciones": "Con cinta"})

    dispositivo = _instalacion(client, token_admin, {"ubicacion": "Estante 2"})

    assert dispositivo["ubicacion"] == "Estante 2"
    assert dispositivo["fecha_instalacion"] == "2026-08-01"
    assert dispositivo["observaciones_instalacion"] == "Con cinta"
    cambios = [(h["campo_base"], h["valor_anterior"], h["valor_nuevo"]) for h in _historial(client, token_auditor).json()]
    assert ("ubicacion", "Estante 1", "Estante 2") in cambios
    assert not any(campo in ("fecha_instalacion", "observaciones_instalacion") and nuevo is None
                   for campo, _, nuevo in cambios)


def test_hu51_null_explicito_si_borra_la_ubicacion(client, token_admin):
    _alta(client, token_admin)
    assert _instalacion(client, token_admin, {"ubicacion": None})["ubicacion"] is None
