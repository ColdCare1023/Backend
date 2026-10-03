from datetime import datetime, timezone

from tests.conftest import auth_header


def _payload(**overrides) -> dict:
    base = {
        "device_id": "FARM-01-CDL",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "temperatura_ambiental": 6.0,
        "humedad_ambiental": 55.0,
        "temperatura_interna": 5.0,
        "apertura_refrigerador": False,
        "estado_conectividad": "online",
    }
    base.update(overrides)
    return base


async def test_ingestar_lectura_estable_clasifica_normal(client, token_tecnico):
    # HU-17: la IA necesita 2+ lecturas previas válidas para tendencia.
    for _i in range(2):
        client.post(
            "/api/lecturas",
            json=_payload(timestamp=datetime.now(tz=timezone.utc).isoformat()),
            headers=auth_header(token_tecnico),
        )

    response = client.post("/api/lecturas", json=_payload(), headers=auth_header(token_tecnico))

    assert response.status_code == 201
    body = response.json()
    assert body["device_id"] == "FARM-01-CDL"
    assert body["nivel_riesgo"] == "normal"


async def test_ingestar_lectura_excursion_critica(client, token_tecnico):
    # HU-18/21/34: sin historial, la IA (nivel_riesgo/model_class) legítimamente
    # queda no_clasificable — pero riesgo_efectivo debe marcar la excursión de
    # todos modos, vía la regla directa HU-21, independiente de la IA.
    response = client.post(
        "/api/lecturas",
        json=_payload(temperatura_interna=18.0, temperatura_ambiental=20.0),
        headers=auth_header(token_tecnico),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["excursion_confirmada"] is True
    assert body["riesgo_efectivo"] == "excursion_critica"


async def test_ingestar_lectura_fuera_de_rango_fisico_es_rechazada(client, token_tecnico):
    response = client.post(
        "/api/lecturas",
        json=_payload(temperatura_interna=500.0),
        headers=auth_header(token_tecnico),
    )
    assert response.status_code == 422


async def test_listar_historial_con_filtro_de_device(client, token_tecnico):
    client.post("/api/lecturas", json=_payload(device_id="FARM-02"), headers=auth_header(token_tecnico))
    client.post("/api/lecturas", json=_payload(device_id="FARM-03"), headers=auth_header(token_tecnico))

    response = client.get(
        "/api/lecturas", params={"device_id": "FARM-02"}, headers=auth_header(token_tecnico)
    )

    assert response.status_code == 200
    lecturas = response.json()
    assert len(lecturas) == 1
    assert lecturas[0]["device_id"] == "FARM-02"


async def test_obtener_lectura_por_id(client, token_tecnico):
    creada = client.post(
        "/api/lecturas", json=_payload(device_id="FARM-04"), headers=auth_header(token_tecnico)
    ).json()

    response = client.get(f"/api/lecturas/{creada['id']}", headers=auth_header(token_tecnico))

    assert response.status_code == 200
    assert response.json()["device_id"] == "FARM-04"


async def test_obtener_lectura_inexistente_devuelve_404(client, token_tecnico):
    response = client.get(
        "/api/lecturas/00000000-0000-0000-0000-000000000000", headers=auth_header(token_tecnico)
    )
    assert response.status_code == 404


async def test_administrador_hereda_permisos_de_ingesta_por_jerarquia_rbac(client, token_admin):
    """RBAC jerárquico: administrador está por encima de técnico/farmacéutico en todas las rutas."""
    response = client.post("/api/lecturas", json=_payload(), headers=auth_header(token_admin))
    assert response.status_code == 201


async def test_tecnico_no_puede_administrar_usuarios(client, token_tecnico):
    """Confirma que la jerarquía RBAC no opera en sentido inverso (mínimo privilegio)."""
    response = client.get("/api/usuarios", headers=auth_header(token_tecnico))
    assert response.status_code == 403
