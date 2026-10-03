"""RF-13: el reporte BPA debe ceñirse al periodo solicitado.

Hallazgo S-03: solo las lecturas se filtraban por fecha. Las alertas y los
registros de trazabilidad se traían del histórico completo, así que un reporte
"de enero" documentaba excursiones y eventos de meses que no le correspondían
— un reporte de cumplimiento que atribuye hechos a un periodo ajeno no vale
como evidencia ante una inspección.
"""

from datetime import datetime, timedelta, timezone

from tests.conftest import auth_header

DEVICE = "FARM-RANGO-01"


def _ingestar_excursion(client, token_tecnico, timestamp: datetime, device_id: str = DEVICE):
    """Una lectura a 20 °C genera alerta (RF-09) y registro de trazabilidad."""
    return client.post(
        "/api/lecturas",
        json={
            "device_id": device_id,
            "timestamp": timestamp.isoformat(),
            "temperatura_ambiental": 22.0,
            "humedad_ambiental": 55.0,
            "temperatura_interna": 20.0,
            "apertura_refrigerador": False,
            "estado_conectividad": "online",
        },
        headers=auth_header(token_tecnico),
    )


async def test_el_reporte_no_incluye_alertas_ni_trazabilidad_fuera_del_periodo(
    client, token_tecnico, token_farmaceutico
):
    ahora = datetime.now(tz=timezone.utc)
    respuesta_ingesta = _ingestar_excursion(client, token_tecnico, ahora)
    assert respuesta_ingesta.status_code in (200, 201)

    # Periodo cerrado ANTES de que ocurriera nada: debe salir vacío.
    desde = ahora - timedelta(days=30)
    hasta = ahora - timedelta(days=20)

    response = client.get(
        "/api/reportes/bpa",
        params={
            "fecha_desde": desde.isoformat(),
            "fecha_hasta": hasta.isoformat(),
            "device_id": DEVICE,
        },
        headers=auth_header(token_farmaceutico),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["lecturas"] == []
    assert body["alertas"] == [], "las alertas deben acotarse al periodo del reporte"
    assert body["registros_trazabilidad"] == [], (
        "la trazabilidad debe acotarse al periodo del reporte"
    )


async def test_el_reporte_si_incluye_lo_ocurrido_dentro_del_periodo(
    client, token_tecnico, token_farmaceutico
):
    """Contraprueba del caso anterior: el filtro no debe vaciar el reporte."""
    ahora = datetime.now(tz=timezone.utc)
    _ingestar_excursion(client, token_tecnico, ahora, device_id="FARM-RANGO-02")

    response = client.get(
        "/api/reportes/bpa",
        params={
            "fecha_desde": (ahora - timedelta(days=1)).isoformat(),
            "fecha_hasta": (ahora + timedelta(days=1)).isoformat(),
            "device_id": "FARM-RANGO-02",
        },
        headers=auth_header(token_farmaceutico),
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["lecturas"]) == 1
    assert len(body["alertas"]) == 1
    assert len(body["registros_trazabilidad"]) >= 1


async def test_rango_invertido_se_rechaza_en_el_reporte_json(client, token_farmaceutico):
    """El endpoint PDF ya validaba el orden de las fechas; el JSON no, y
    devolvía 200 con un reporte imposible."""
    ahora = datetime.now(tz=timezone.utc)

    response = client.get(
        "/api/reportes/bpa",
        params={
            "fecha_desde": ahora.isoformat(),
            "fecha_hasta": (ahora - timedelta(days=5)).isoformat(),
        },
        headers=auth_header(token_farmaceutico),
    )

    assert response.status_code == 422
    assert "fecha_desde" in response.json()["detail"]


async def test_periodo_desmesurado_se_rechaza(client, token_farmaceutico):
    """OWASP API4: sin techo de periodo, una sola petición puede pedir la
    materialización de todo el histórico en una instancia de 512 MB."""
    ahora = datetime.now(tz=timezone.utc)

    response = client.get(
        "/api/reportes/bpa",
        params={
            "fecha_desde": (ahora - timedelta(days=4000)).isoformat(),
            "fecha_hasta": ahora.isoformat(),
        },
        headers=auth_header(token_farmaceutico),
    )

    assert response.status_code == 422
    assert "días" in response.json()["detail"]


async def test_el_reporte_bpa_tiene_cuota_propia_por_usuario(client, token_farmaceutico):
    """Exportar es la operación más cara de la API; el límite global por IP
    (240/min) la dejaba repetir muy por encima de lo razonable."""
    ahora = datetime.now(tz=timezone.utc)
    params = {
        "fecha_desde": (ahora - timedelta(days=1)).isoformat(),
        "fecha_hasta": ahora.isoformat(),
    }

    codigos = [
        client.get("/api/reportes/bpa", params=params, headers=auth_header(token_farmaceutico)).status_code
        for _ in range(12)
    ]

    assert 429 in codigos
    assert codigos[0] == 200
