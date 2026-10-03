"""HU-46 (gobierno y versionado del modelo IA) y HU-47 (consulta de
trazabilidad de la inferencia) del backlog de 51 HU finales."""

from tests.conftest import auth_header

DEVICE_ID = "FARM-HU46-01"
HASH_VALIDO = "a" * 64


def _version_payload(**overrides) -> dict:
    base = {
        "version": "3.1.0-test",
        "model_hash": HASH_VALIDO,
        "feature_schema_version": "1",
        "dataset_version": "dataset-2026-08",
        "scikit_learn_version": "1.5.0",
        "python_version": "3.12.0",
        "trained_at": "2026-08-01T00:00:00Z",
    }
    base.update(overrides)
    return base


def test_registrar_version_no_la_activa(client, token_admin):
    respuesta = client.post(
        "/api/ia/modelo/versiones", json=_version_payload(), headers=auth_header(token_admin)
    )
    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["version"] == "3.1.0-test"
    assert cuerpo["activa"] is False


def test_registrar_version_duplicada_es_conflicto(client, token_admin):
    client.post("/api/ia/modelo/versiones", json=_version_payload(), headers=auth_header(token_admin))
    respuesta = client.post(
        "/api/ia/modelo/versiones", json=_version_payload(), headers=auth_header(token_admin)
    )
    assert respuesta.status_code == 409


def test_activar_version_no_registrada_se_rechaza(client, token_admin):
    respuesta = client.post(
        "/api/ia/modelo/versiones/no-existe-9.9.9/activar", headers=auth_header(token_admin)
    )
    assert respuesta.status_code == 404


def test_activar_version_registrada_la_marca_activa(client, token_admin):
    client.post("/api/ia/modelo/versiones", json=_version_payload(), headers=auth_header(token_admin))

    respuesta = client.post(
        "/api/ia/modelo/versiones/3.1.0-test/activar", headers=auth_header(token_admin)
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["activa"] is True
    assert respuesta.json()["activada_en"] is not None


def test_activar_una_version_desactiva_la_anterior(client, token_admin):
    client.post(
        "/api/ia/modelo/versiones",
        json=_version_payload(version="3.1.0-test"),
        headers=auth_header(token_admin),
    )
    client.post(
        "/api/ia/modelo/versiones",
        json=_version_payload(version="3.2.0-test"),
        headers=auth_header(token_admin),
    )
    client.post("/api/ia/modelo/versiones/3.1.0-test/activar", headers=auth_header(token_admin))
    client.post("/api/ia/modelo/versiones/3.2.0-test/activar", headers=auth_header(token_admin))

    versiones = {
        v["version"]: v
        for v in client.get("/api/ia/modelo/versiones", headers=auth_header(token_admin)).json()
    }
    assert versiones["3.1.0-test"]["activa"] is False
    assert versiones["3.1.0-test"]["desactivada_en"] is not None
    assert versiones["3.2.0-test"]["activa"] is True


def test_no_admin_no_puede_registrar_version(client, token_farmaceutico):
    respuesta = client.post(
        "/api/ia/modelo/versiones", json=_version_payload(), headers=auth_header(token_farmaceutico)
    )
    assert respuesta.status_code == 403


def test_registro_y_activacion_quedan_encadenados(client, token_admin):
    client.post("/api/ia/modelo/versiones", json=_version_payload(), headers=auth_header(token_admin))
    client.post("/api/ia/modelo/versiones/3.1.0-test/activar", headers=auth_header(token_admin))

    trazabilidad = client.get("/api/trazabilidad", headers=auth_header(token_admin)).json()
    tipos = [r["tipo_evento"] for r in trazabilidad]
    assert "MODELO_IA_VERSION_REGISTRADA" in tipos
    assert "MODELO_IA_VERSION_ACTIVADA" in tipos


def test_consultar_inferencia_de_lectura_devuelve_evidencia(client, token_tecnico, token_farmaceutico):
    # Dos lecturas previas para que la tercera SÍ tenga inferencia completa.
    for minutos, temp in ((10, 5.0), (5, 5.0)):
        client.post(
            "/api/lecturas",
            json={
                "device_id": DEVICE_ID,
                "timestamp": f"2026-09-02T00:{minutos:02d}:00Z",
                "temperatura_interna": temp,
                "temperatura_ambiental": 21.0,
                "humedad_ambiental": 55.0,
                "apertura_refrigerador": False,
                "estado_conectividad": "online",
            },
            headers=auth_header(token_tecnico),
        )
    tercera = client.post(
        "/api/lecturas",
        json={
            "device_id": DEVICE_ID,
            "timestamp": "2026-09-02T00:15:00Z",
            "temperatura_interna": 5.0,
            "temperatura_ambiental": 21.0,
            "humedad_ambiental": 55.0,
            "apertura_refrigerador": False,
            "estado_conectividad": "online",
        },
        headers=auth_header(token_tecnico),
    ).json()

    respuesta = client.get(
        f"/api/ia/inferencia/{tercera['id']}", headers=auth_header(token_farmaceutico)
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["lectura_id"] == tercera["id"]
    assert cuerpo["device_id"] == DEVICE_ID
    if cuerpo["modelo_version"] is not None:
        # Solo si hay un modelo Random Forest real cargado en este entorno.
        assert cuerpo["probabilidades_por_clase"] is not None
        assert cuerpo["vector_features"] is not None


def test_consultar_inferencia_de_lectura_inexistente_da_404(client, token_farmaceutico):
    respuesta = client.get(
        "/api/ia/inferencia/00000000-0000-0000-0000-000000000000",
        headers=auth_header(token_farmaceutico),
    )
    assert respuesta.status_code == 404


def test_consultar_inferencia_no_es_de_escritura(client, token_farmaceutico):
    """HU-47 criterio 3: no existe ningún verbo de escritura para este
    recurso — se comprueba negativamente contra el esquema OpenAPI."""
    esquema = client.get("/openapi.json").json()
    ruta = esquema["paths"].get("/api/ia/inferencia/{lectura_id}", {})
    assert set(ruta.keys()) == {"get"}
