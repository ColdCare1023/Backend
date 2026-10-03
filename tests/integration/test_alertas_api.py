from datetime import datetime, timezone

from tests.conftest import auth_header


def _payload_excursion_critica(device_id: str = "FARM-ALERTA-01") -> dict:
    return {
        "device_id": device_id,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "temperatura_ambiental": 20.0,
        "humedad_ambiental": 60.0,
        "temperatura_interna": 18.0,
        "apertura_refrigerador": True,
        "estado_conectividad": "online",
    }


async def test_lectura_critica_genera_alerta_automaticamente(client, token_tecnico):
    client.post("/api/lecturas", json=_payload_excursion_critica(), headers=auth_header(token_tecnico))

    response = client.get(
        "/api/alertas", params={"device_id": "FARM-ALERTA-01"}, headers=auth_header(token_tecnico)
    )

    assert response.status_code == 200
    alertas = response.json()
    assert len(alertas) == 1
    assert alertas[0]["nivel_riesgo"] == "excursion_critica"
    assert alertas[0]["revisada"] is False


async def test_lectura_normal_no_genera_alerta(client, token_tecnico):
    client.post(
        "/api/lecturas",
        json={
            "device_id": "FARM-SIN-ALERTA",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "temperatura_ambiental": 6.0,
            "humedad_ambiental": 55.0,
            "temperatura_interna": 5.0,
            "apertura_refrigerador": False,
            "estado_conectividad": "online",
        },
        headers=auth_header(token_tecnico),
    )

    response = client.get(
        "/api/alertas", params={"device_id": "FARM-SIN-ALERTA"}, headers=auth_header(token_tecnico)
    )
    assert response.json() == []


async def test_farmaceutico_puede_revisar_alerta(client, token_tecnico, token_farmaceutico):
    client.post(
        "/api/lecturas", json=_payload_excursion_critica("FARM-REVISAR"), headers=auth_header(token_tecnico)
    )
    alerta = client.get(
        "/api/alertas", params={"device_id": "FARM-REVISAR"}, headers=auth_header(token_tecnico)
    ).json()[0]

    response = client.patch(
        f"/api/alertas/{alerta['id']}/revisar", headers=auth_header(token_farmaceutico)
    )

    assert response.status_code == 200
    assert response.json()["revisada"] is True


async def test_tecnico_no_puede_revisar_alerta(client, token_tecnico):
    client.post(
        "/api/lecturas",
        json=_payload_excursion_critica("FARM-REVISAR-TECNICO"),
        headers=auth_header(token_tecnico),
    )
    alerta = client.get(
        "/api/alertas", params={"device_id": "FARM-REVISAR-TECNICO"}, headers=auth_header(token_tecnico)
    ).json()[0]

    response = client.patch(f"/api/alertas/{alerta['id']}/revisar", headers=auth_header(token_tecnico))
    assert response.status_code == 403


async def test_revisar_alerta_encadena_en_la_cadena_del_dispositivo(
    client, token_tecnico, token_farmaceutico, token_admin
):
    """HU-25/HU-28: el reconocimiento de una alerta debe quedar en la MISMA
    cadena que las lecturas de ese dispositivo (chain_id=device_id) — no en
    la cadena de sistema — para que un auditor pueda reconstruir el ciclo
    completo de la alerta verificando una sola cadena."""
    client.post(
        "/api/lecturas", json=_payload_excursion_critica("FARM-CHAIN-01"), headers=auth_header(token_tecnico)
    )
    alerta = client.get(
        "/api/alertas", params={"device_id": "FARM-CHAIN-01"}, headers=auth_header(token_tecnico)
    ).json()[0]

    client.patch(f"/api/alertas/{alerta['id']}/revisar", headers=auth_header(token_farmaceutico))

    trazabilidad = client.get(
        "/api/trazabilidad",
        params={"tipo_evento": "AUDIT_LOG", "device_id": "FARM-CHAIN-01"},
        headers=auth_header(token_admin),
    ).json()
    eventos_revisar = [r for r in trazabilidad if r["device_id"] == "FARM-CHAIN-01"]
    assert len(eventos_revisar) >= 1
    assert all(r["chain_id"] == "FARM-CHAIN-01" for r in eventos_revisar)


async def test_registrar_accion_correctiva_sobre_alerta(client, token_tecnico):
    client.post(
        "/api/lecturas",
        json=_payload_excursion_critica("FARM-ACCION"),
        headers=auth_header(token_tecnico),
    )
    alerta = client.get(
        "/api/alertas", params={"device_id": "FARM-ACCION"}, headers=auth_header(token_tecnico)
    ).json()[0]

    response = client.post(
        f"/api/alertas/{alerta['id']}/acciones-correctivas",
        json={"descripcion": "Se trasladó el medicamento a refrigerador de respaldo."},
        headers=auth_header(token_tecnico),
    )

    assert response.status_code == 201
    assert response.json()["descripcion"] == "Se trasladó el medicamento a refrigerador de respaldo."


async def test_listar_acciones_correctivas_devuelve_ciclo_cronologico_con_rectificacion(
    client, token_tecnico
):
    """HU-23 criterio 4 / HU-28: la cronología de atención de una alerta
    incluye la acción original y su rectificación, en orden, sin que la
    corrección sobrescriba el registro anterior."""
    client.post(
        "/api/lecturas",
        json=_payload_excursion_critica("FARM-CICLO"),
        headers=auth_header(token_tecnico),
    )
    alerta = client.get(
        "/api/alertas", params={"device_id": "FARM-CICLO"}, headers=auth_header(token_tecnico)
    ).json()[0]

    original = client.post(
        f"/api/alertas/{alerta['id']}/acciones-correctivas",
        json={"descripcion": "Justificación inicial."},
        headers=auth_header(token_tecnico),
    ).json()

    client.post(
        f"/api/alertas/acciones-correctivas/{original['id']}/rectificar",
        json={"descripcion": "Justificación corregida con el detalle completo."},
        headers=auth_header(token_tecnico),
    )

    ciclo = client.get(
        f"/api/alertas/{alerta['id']}/acciones-correctivas", headers=auth_header(token_tecnico)
    ).json()

    assert len(ciclo) == 2
    assert ciclo[0]["descripcion"] == "Justificación inicial."
    assert ciclo[0]["corrige_accion_id"] is None
    assert ciclo[1]["descripcion"] == "Justificación corregida con el detalle completo."
    assert ciclo[1]["corrige_accion_id"] == original["id"]


async def test_listar_acciones_correctivas_de_alerta_inexistente_devuelve_404(client, token_tecnico):
    import uuid

    response = client.get(
        f"/api/alertas/{uuid.uuid4()}/acciones-correctivas", headers=auth_header(token_tecnico)
    )
    assert response.status_code == 404


async def test_accion_correctiva_sobre_alerta_inexistente_devuelve_404(client, token_tecnico):
    response = client.post(
        "/api/alertas/00000000-0000-0000-0000-000000000000/acciones-correctivas",
        json={"descripcion": "No debería aplicar"},
        headers=auth_header(token_tecnico),
    )
    assert response.status_code == 404
