from datetime import datetime, timedelta, timezone

from tests.conftest import auth_header


async def test_farmaceutico_puede_exportar_reporte_bpa(client, token_tecnico, token_farmaceutico):
    client.post(
        "/api/lecturas",
        json={
            "device_id": "FARM-REPORTE-01",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "temperatura_ambiental": 6.0,
            "humedad_ambiental": 55.0,
            "temperatura_interna": 5.0,
            "apertura_refrigerador": False,
            "estado_conectividad": "online",
        },
        headers=auth_header(token_tecnico),
    )

    desde = (datetime.now(tz=timezone.utc) - timedelta(days=1)).isoformat()
    hasta = (datetime.now(tz=timezone.utc) + timedelta(days=1)).isoformat()

    response = client.get(
        "/api/reportes/bpa",
        params={"fecha_desde": desde, "fecha_hasta": hasta, "device_id": "FARM-REPORTE-01"},
        headers=auth_header(token_farmaceutico),
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["lecturas"]) == 1
    assert body["device_id"] == "FARM-REPORTE-01"


async def test_tecnico_no_puede_exportar_reporte_bpa(client, token_tecnico):
    desde = (datetime.now(tz=timezone.utc) - timedelta(days=1)).isoformat()
    hasta = datetime.now(tz=timezone.utc).isoformat()

    response = client.get(
        "/api/reportes/bpa",
        params={"fecha_desde": desde, "fecha_hasta": hasta},
        headers=auth_header(token_tecnico),
    )
    assert response.status_code == 403
