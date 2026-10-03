from datetime import datetime, timezone

from tests.conftest import auth_header


async def test_cada_lectura_genera_registro_de_trazabilidad(client, token_tecnico):
    client.post(
        "/api/lecturas",
        json={
            "device_id": "FARM-TRAZA-01",
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
        "/api/trazabilidad",
        params={"device_id": "FARM-TRAZA-01", "tipo_evento": "LECTURA_TERMICA"},
        headers=auth_header(token_tecnico),
    )

    assert response.status_code == 200
    registros = response.json()
    assert len(registros) == 1
    assert registros[0]["previous_hash"] is not None
    assert len(registros[0]["hash_actual"]) == 64


async def test_verificar_integridad_de_cadena_vacia_es_integra(client, token_tecnico):
    response = client.get("/api/trazabilidad/verificar", headers=auth_header(token_tecnico))
    assert response.status_code == 200
    assert response.json()["integra"] is True


async def test_verificar_integridad_tras_varias_lecturas_es_integra(client, token_tecnico):
    for i in range(3):
        client.post(
            "/api/lecturas",
            json={
                "device_id": f"FARM-TRAZA-CADENA-{i}",
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "temperatura_ambiental": 6.0,
                "humedad_ambiental": 55.0,
                "temperatura_interna": 5.0,
                "apertura_refrigerador": False,
                "estado_conectividad": "online",
            },
            headers=auth_header(token_tecnico),
        )

    response = client.get("/api/trazabilidad/verificar", headers=auth_header(token_tecnico))
    body = response.json()
    assert body["integra"] is True
    assert body["total_registros"] >= 3


async def test_integridad_se_mantiene_con_lecturas_criticas_que_generan_alerta(
    client, token_tecnico
):
    """Regresión: los registros ALERTA_TERMICA usan timestamp del servidor
    (aware); la verificación debe seguir siendo consistente tras el round-trip
    por la base de datos (SQLite devuelve datetimes naive)."""
    client.post(
        "/api/lecturas",
        json={
            "device_id": "FARM-TRAZA-CRITICA",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "temperatura_ambiental": 20.0,
            "humedad_ambiental": 60.0,
            "temperatura_interna": 18.0,
            "apertura_refrigerador": True,
            "estado_conectividad": "online",
        },
        headers=auth_header(token_tecnico),
    )

    response = client.get("/api/trazabilidad/verificar", headers=auth_header(token_tecnico))
    body = response.json()
    assert body["integra"] is True
    assert body["total_registros"] >= 2  # LECTURA_TERMICA + ALERTA_TERMICA
