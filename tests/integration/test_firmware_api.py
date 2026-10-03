from datetime import datetime, timezone

from tests.conftest import auth_header

_HASH_VALIDO = "a" * 64


def _ingestar_lectura(client, token, device_id: str):
    return client.post(
        "/api/lecturas",
        json={
            "device_id": device_id,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "temperatura_ambiental": 22.0,
            "humedad_ambiental": 40.0,
            "temperatura_interna": 4.0,
            "apertura_refrigerador": False,
            "estado_conectividad": "online",
        },
        headers=auth_header(token),
    )


def _crear_release(client, token_admin, version: str):
    return client.post(
        "/api/firmware/releases",
        json={"version": version, "hash_sha256": _HASH_VALIDO, "descripcion": f"parche {version}"},
        headers=auth_header(token_admin),
    )


def test_admin_prepara_release(client, token_admin):
    response = _crear_release(client, token_admin, "1.1.0")
    assert response.status_code == 201
    body = response.json()
    assert body["version"] == "1.1.0"
    assert body["hash_sha256"] == _HASH_VALIDO


def test_release_duplicada_es_conflicto(client, token_admin):
    _crear_release(client, token_admin, "1.2.0")
    segunda = _crear_release(client, token_admin, "1.2.0")
    assert segunda.status_code == 409


def test_tecnico_no_puede_preparar_release(client, token_tecnico):
    response = client.post(
        "/api/firmware/releases",
        json={"version": "1.3.0", "hash_sha256": _HASH_VALIDO, "descripcion": "no autorizado"},
        headers=auth_header(token_tecnico),
    )
    assert response.status_code == 403


def test_listar_releases(client, token_admin):
    _crear_release(client, token_admin, "1.4.0")
    response = client.get("/api/firmware/releases", headers=auth_header(token_admin))
    assert response.status_code == 200
    assert any(r["version"] == "1.4.0" for r in response.json())


def test_programar_despliegue_exitoso(client, token_admin, token_tecnico):
    _ingestar_lectura(client, token_tecnico, "esp32-ota-01")  # firmware_version default 1.0.0
    _crear_release(client, token_admin, "1.5.0")

    response = client.post(
        "/api/firmware/despliegues",
        json={"device_id": "esp32-ota-01", "version_objetivo": "1.5.0"},
        headers=auth_header(token_admin),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["estado"] == "programado"


def test_programar_despliegue_rechaza_downgrade(client, token_admin, token_tecnico):
    _ingestar_lectura(client, token_tecnico, "esp32-ota-02")
    _crear_release(client, token_admin, "1.6.0")
    # Sube primero a 1.6.0 vía ejecución para luego intentar bajar de versión.
    programar = client.post(
        "/api/firmware/despliegues",
        json={"device_id": "esp32-ota-02", "version_objetivo": "1.6.0"},
        headers=auth_header(token_admin),
    )
    despliegue_id = programar.json()["id"]
    ejecutar = client.post(
        f"/api/firmware/despliegues/{despliegue_id}/ejecutar", headers=auth_header(token_admin)
    )
    assert ejecutar.json()["estado"] == "exitoso"

    _crear_release(client, token_admin, "1.0.9")
    downgrade = client.post(
        "/api/firmware/despliegues",
        json={"device_id": "esp32-ota-02", "version_objetivo": "1.0.9"},
        headers=auth_header(token_admin),
    )
    assert downgrade.status_code == 409


def test_programar_despliegue_dispositivo_inexistente_404(client, token_admin):
    _crear_release(client, token_admin, "1.7.0")
    response = client.post(
        "/api/firmware/despliegues",
        json={"device_id": "no-existe", "version_objetivo": "1.7.0"},
        headers=auth_header(token_admin),
    )
    assert response.status_code == 404


def test_programar_despliegue_release_inexistente_404(client, token_admin, token_tecnico):
    _ingestar_lectura(client, token_tecnico, "esp32-ota-03")
    response = client.post(
        "/api/firmware/despliegues",
        json={"device_id": "esp32-ota-03", "version_objetivo": "9.9.9"},
        headers=auth_header(token_admin),
    )
    assert response.status_code == 404


def test_ejecutar_despliegue_actualiza_firmware_del_dispositivo(client, token_admin, token_tecnico):
    _ingestar_lectura(client, token_tecnico, "esp32-ota-04")
    _crear_release(client, token_admin, "2.0.0")
    programar = client.post(
        "/api/firmware/despliegues",
        json={"device_id": "esp32-ota-04", "version_objetivo": "2.0.0"},
        headers=auth_header(token_admin),
    )
    despliegue_id = programar.json()["id"]

    ejecutar = client.post(
        f"/api/firmware/despliegues/{despliegue_id}/ejecutar", headers=auth_header(token_admin)
    )
    assert ejecutar.status_code == 200
    assert ejecutar.json()["estado"] == "exitoso"

    dispositivos = client.get("/api/dispositivos", headers=auth_header(token_admin)).json()
    dispositivo = next(d for d in dispositivos if d["id"] == "esp32-ota-04")
    assert dispositivo["firmware_version"] == "2.0.0"


def test_ejecutar_despliegue_ya_procesado_es_conflicto(client, token_admin, token_tecnico):
    _ingestar_lectura(client, token_tecnico, "esp32-ota-05")
    _crear_release(client, token_admin, "2.1.0")
    programar = client.post(
        "/api/firmware/despliegues",
        json={"device_id": "esp32-ota-05", "version_objetivo": "2.1.0"},
        headers=auth_header(token_admin),
    )
    despliegue_id = programar.json()["id"]
    client.post(f"/api/firmware/despliegues/{despliegue_id}/ejecutar", headers=auth_header(token_admin))

    segunda_ejecucion = client.post(
        f"/api/firmware/despliegues/{despliegue_id}/ejecutar", headers=auth_header(token_admin)
    )
    assert segunda_ejecucion.status_code == 409


def test_firmware_eventos_quedan_en_trazabilidad_encadenada(client, token_admin, token_tecnico):
    _ingestar_lectura(client, token_tecnico, "esp32-ota-06")
    _crear_release(client, token_admin, "3.0.0")
    programar = client.post(
        "/api/firmware/despliegues",
        json={"device_id": "esp32-ota-06", "version_objetivo": "3.0.0"},
        headers=auth_header(token_admin),
    )
    despliegue_id = programar.json()["id"]
    client.post(f"/api/firmware/despliegues/{despliegue_id}/ejecutar", headers=auth_header(token_admin))

    trazabilidad = client.get(
        "/api/trazabilidad", params={"tipo_evento": "FIRMWARE_ACTUALIZADO"}, headers=auth_header(token_tecnico)
    )
    assert trazabilidad.status_code == 200
    assert any(r["payload"]["device_id"] == "esp32-ota-06" for r in trazabilidad.json())
