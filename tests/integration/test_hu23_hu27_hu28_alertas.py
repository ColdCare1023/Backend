"""HU-23 (máquina de estados PENDIENTE/RECONOCIDA/ATENDIDA de alertas
críticas) y HU-27/HU-28 (concurrencia y rectificación de acciones
correctivas) del backlog de 51 HU finales."""

from datetime import datetime, timezone

from tests.conftest import auth_header

DEVICE_ID = "FARM-HU23-01"


def _payload(**overrides) -> dict:
    base = {
        "device_id": DEVICE_ID,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "temperatura_interna": 15.0,  # fuera de rango: excursión crítica
        "temperatura_ambiental": 21.0,
        "humedad_ambiental": 55.0,
        "apertura_refrigerador": False,
        "estado_conectividad": "online",
    }
    base.update(overrides)
    return base


def _crear_alerta_critica(client, token) -> str:
    respuesta = client.post("/api/lecturas", json=_payload(), headers=auth_header(token))
    assert respuesta.status_code == 201
    alertas = client.get(
        "/api/alertas", params={"device_id": DEVICE_ID}, headers=auth_header(token)
    ).json()
    assert len(alertas) == 1
    return alertas[0]["id"]


def test_alerta_nace_pendiente(client, token_tecnico):
    alerta_id = _crear_alerta_critica(client, token_tecnico)
    alerta = client.get(
        "/api/alertas", params={"device_id": DEVICE_ID}, headers=auth_header(token_tecnico)
    ).json()[0]
    assert alerta["id"] == alerta_id
    assert alerta["estado"] == "pendiente"
    assert alerta["reconocida_en"] is None
    assert alerta["atendida_en"] is None


def test_reconocer_transiciona_a_reconocida(client, token_tecnico, token_farmaceutico):
    alerta_id = _crear_alerta_critica(client, token_tecnico)

    respuesta = client.patch(
        f"/api/alertas/{alerta_id}/revisar", headers=auth_header(token_farmaceutico)
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["estado"] == "reconocida"
    assert cuerpo["reconocida_en"] is not None
    assert cuerpo["revisada"] is True


def test_reconocer_dos_veces_es_conflicto(client, token_tecnico, token_farmaceutico):
    alerta_id = _crear_alerta_critica(client, token_tecnico)
    client.patch(f"/api/alertas/{alerta_id}/revisar", headers=auth_header(token_farmaceutico))

    segunda = client.patch(f"/api/alertas/{alerta_id}/revisar", headers=auth_header(token_farmaceutico))
    assert segunda.status_code == 409


def test_registrar_accion_correctiva_transiciona_a_atendida(client, token_tecnico, token_farmaceutico):
    alerta_id = _crear_alerta_critica(client, token_tecnico)
    client.patch(f"/api/alertas/{alerta_id}/revisar", headers=auth_header(token_farmaceutico))

    respuesta = client.post(
        f"/api/alertas/{alerta_id}/acciones-correctivas",
        json={"descripcion": "Se trasladaron los medicamentos a un refrigerador de respaldo."},
        headers=auth_header(token_tecnico),
    )
    assert respuesta.status_code == 201

    alerta = client.get(
        "/api/alertas", params={"device_id": DEVICE_ID}, headers=auth_header(token_tecnico)
    ).json()[0]
    assert alerta["estado"] == "atendida"
    assert alerta["atendida_en"] is not None


def test_accion_correctiva_concurrente_sobre_alerta_atendida_es_rechazada(
    client, token_tecnico, token_farmaceutico
):
    """HU-27 Escenario 2: dos usuarios intentan atender la misma alerta casi
    a la vez — el segundo debe recibir 409, la alerta ya no está vigente."""
    alerta_id = _crear_alerta_critica(client, token_tecnico)

    primera = client.post(
        f"/api/alertas/{alerta_id}/acciones-correctivas",
        json={"descripcion": "Primera acción: se documentó la excursión."},
        headers=auth_header(token_tecnico),
    )
    assert primera.status_code == 201

    segunda = client.post(
        f"/api/alertas/{alerta_id}/acciones-correctivas",
        json={"descripcion": "Segunda acción, llegó tarde."},
        headers=auth_header(token_farmaceutico),
    )
    assert segunda.status_code == 409


def test_accion_correctiva_sin_descripcion_se_rechaza(client, token_tecnico):
    alerta_id = _crear_alerta_critica(client, token_tecnico)

    respuesta = client.post(
        f"/api/alertas/{alerta_id}/acciones-correctivas",
        json={"descripcion": ""},
        headers=auth_header(token_tecnico),
    )
    assert respuesta.status_code == 422


def test_rectificar_accion_correctiva_crea_evento_nuevo_sin_sobrescribir(client, token_tecnico):
    alerta_id = _crear_alerta_critica(client, token_tecnico)
    original = client.post(
        f"/api/alertas/{alerta_id}/acciones-correctivas",
        json={"descripcion": "Descripción con un error de tipeo."},
        headers=auth_header(token_tecnico),
    ).json()

    rectificacion = client.post(
        f"/api/alertas/acciones-correctivas/{original['id']}/rectificar",
        json={"descripcion": "Descripción corregida, sin el error."},
        headers=auth_header(token_tecnico),
    )
    assert rectificacion.status_code == 201
    cuerpo = rectificacion.json()
    assert cuerpo["corrige_accion_id"] == original["id"]
    assert cuerpo["id"] != original["id"]
    assert cuerpo["descripcion"] == "Descripción corregida, sin el error."

    # La acción original NO se modificó.
    trazabilidad = client.get(
        "/api/trazabilidad", params={"tipo_evento": "ACCION_CORRECTIVA_RECTIFICADA"},
        headers=auth_header(token_tecnico),
    ).json()
    assert len(trazabilidad) == 1
