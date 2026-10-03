from datetime import datetime, timezone

from tests.conftest import auth_header


def _ingestar_lectura(client, token, device_id: str, timestamp: datetime | None = None):
    return client.post(
        "/api/lecturas",
        json={
            "device_id": device_id,
            "timestamp": (timestamp or datetime.now(tz=timezone.utc)).isoformat(),
            "temperatura_ambiental": 22.0,
            "humedad_ambiental": 40.0,
            "temperatura_interna": 4.0,
            "apertura_refrigerador": False,
            "estado_conectividad": "online",
        },
        headers=auth_header(token),
    )


def test_admin_lista_dispositivos(client, token_admin, token_tecnico):
    assert _ingestar_lectura(client, token_tecnico, "esp32-baja-01").status_code == 201

    response = client.get("/api/dispositivos", headers=auth_header(token_admin))
    assert response.status_code == 200
    ids = [d["id"] for d in response.json()]
    assert "esp32-baja-01" in ids


def test_tecnico_no_puede_listar_dispositivos(client, token_tecnico):
    response = client.get("/api/dispositivos", headers=auth_header(token_tecnico))
    assert response.status_code == 403


def test_admin_da_de_baja_dispositivo(client, token_admin, token_tecnico):
    _ingestar_lectura(client, token_tecnico, "esp32-baja-02")

    response = client.post(
        "/api/dispositivos/esp32-baja-02/baja",
        json={"motivo": "falla_hardware", "descripcion": "Sensor DHT22 dañado"},
        headers=auth_header(token_admin),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["activo"] is False
    assert body["motivo_baja"] == "falla_hardware"
    assert body["dado_de_baja_en"] is not None


def test_baja_con_reemplazo_vincula_device_nuevo(client, token_admin, token_tecnico):
    _ingestar_lectura(client, token_tecnico, "esp32-viejo")

    response = client.post(
        "/api/dispositivos/esp32-viejo/baja",
        json={
            "motivo": "reemplazo",
            "descripcion": "Cambio de placa",
            "device_id_reemplazo": "esp32-nuevo",
        },
        headers=auth_header(token_admin),
    )
    assert response.status_code == 200

    listado = client.get("/api/dispositivos", headers=auth_header(token_admin)).json()
    nuevo = next(d for d in listado if d["id"] == "esp32-nuevo")
    assert nuevo["reemplaza_a_device_id"] == "esp32-viejo"

    viejo = next(d for d in listado if d["id"] == "esp32-viejo")
    assert viejo["activo"] is False


def test_baja_con_motivo_invalido_rechaza(client, token_admin, token_tecnico):
    _ingestar_lectura(client, token_tecnico, "esp32-motivo-malo")

    response = client.post(
        "/api/dispositivos/esp32-motivo-malo/baja",
        json={"motivo": "porque_si"},
        headers=auth_header(token_admin),
    )
    assert response.status_code == 409


def test_baja_dispositivo_inexistente_404(client, token_admin):
    response = client.post(
        "/api/dispositivos/no-existe/baja",
        json={"motivo": "falla_hardware"},
        headers=auth_header(token_admin),
    )
    assert response.status_code == 404


def test_baja_dispositivo_ya_dado_de_baja_es_conflicto(client, token_admin, token_tecnico):
    _ingestar_lectura(client, token_tecnico, "esp32-doble-baja")
    primero = client.post(
        "/api/dispositivos/esp32-doble-baja/baja",
        json={"motivo": "fin_de_servicio"},
        headers=auth_header(token_admin),
    )
    assert primero.status_code == 200

    segundo = client.post(
        "/api/dispositivos/esp32-doble-baja/baja",
        json={"motivo": "fin_de_servicio"},
        headers=auth_header(token_admin),
    )
    assert segundo.status_code == 409


def test_baja_dispositivo_no_borra_lecturas_historicas(client, token_admin, token_tecnico):
    _ingestar_lectura(client, token_tecnico, "esp32-historico")
    client.post(
        "/api/dispositivos/esp32-historico/baja",
        json={"motivo": "mantenimiento"},
        headers=auth_header(token_admin),
    )

    trazabilidad = client.get(
        "/api/trazabilidad",
        params={"device_id": "esp32-historico"},
        headers=auth_header(token_tecnico),
    )
    assert trazabilidad.status_code == 200
    tipos = [r["tipo_evento"] for r in trazabilidad.json()]
    assert "BAJA_HARDWARE" in tipos
