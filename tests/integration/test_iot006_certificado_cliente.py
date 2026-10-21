"""IOT-006: el token MQTT ya no es la única prueba de identidad del nodo.

Con TLS mutuo, EMQX verifica el certificado de cliente y pasa su CN al
webhook (`cert_common_name`). El CN debe ser el device_id, y la primera
conexión con certificado liga el dispositivo a él: desde entonces quien solo
tenga el token (copiado del nodo o de un registro) no se conecta.
"""

import pytest
from sqlalchemy import select

from src.infrastructure.config import get_settings
from src.infrastructure.database.models import TraceabilityRecordModel
from tests.conftest import auth_header

DEVICE_ID = "FARM-IOT006-01"
OTRO_DEVICE = "FARM-IOT006-02"
SECRETO_BROKER = "secreto-del-broker-para-pruebas-0123456789"
CABECERA_BROKER = {"X-Broker-Auth": SECRETO_BROKER}


@pytest.fixture(autouse=True)
def _broker_configurado(monkeypatch):
    monkeypatch.setenv("BROKER_WEBHOOK_SECRET", SECRETO_BROKER)
    monkeypatch.delenv("BROKER_EXIGIR_CERTIFICADO", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def token_nodo(client, token_tecnico, token_admin) -> str:
    client.post(
        "/api/lecturas",
        json={
            "device_id": DEVICE_ID,
            "timestamp": "2026-09-01T12:00:00Z",
            "temperatura_interna": 5.0,
            "temperatura_ambiental": 21.0,
            "humedad_ambiental": 55.0,
            "apertura_refrigerador": False,
            "estado_conectividad": "online",
        },
        headers=auth_header(token_tecnico),
    )
    return client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar", json={"motivo": "alta"}, headers=auth_header(token_admin)
    ).json()["token"]


def _autenticar(client, token: str, cn: str | None = None, device_id: str = DEVICE_ID) -> str:
    cuerpo = {"device_id": device_id, "token": token}
    if cn is not None:
        cuerpo["cert_common_name"] = cn
    respuesta = client.post("/api/dispositivos/autenticar", json=cuerpo, headers=CABECERA_BROKER)
    assert respuesta.status_code == 200
    return respuesta.json()["result"]


async def _vinculos(fabrica) -> int:
    async with fabrica() as session:
        filas = (
            await session.execute(
                select(TraceabilityRecordModel).where(TraceabilityRecordModel.tipo_evento == "CERTIFICADO_DISPOSITIVO_VINCULADO")
            )
        ).scalars().all()
    return len(filas)


@pytest.mark.parametrize("sin_certificado", [None, "", "undefined"], ids=["ausente", "vacio", "undefined"])
def test_sin_certificado_y_sin_vinculo_sigue_bastando_el_token(client, token_nodo, sin_certificado):
    assert _autenticar(client, token_nodo, sin_certificado) == "allow"


async def test_la_primera_conexion_con_certificado_liga_el_nodo(client, token_nodo, db_session_factory):
    assert _autenticar(client, token_nodo, DEVICE_ID) == "allow"

    # Quien solo tiene el token copiado ya no se conecta.
    assert _autenticar(client, token_nodo) == "deny"
    assert _autenticar(client, token_nodo, "undefined") == "deny"
    assert _autenticar(client, token_nodo, DEVICE_ID) == "allow"
    assert await _vinculos(db_session_factory) == 1


async def test_certificado_de_otro_nodo_se_rechaza_y_no_liga(client, token_nodo, db_session_factory):
    assert _autenticar(client, token_nodo, OTRO_DEVICE) == "deny"

    assert _autenticar(client, token_nodo) == "allow"
    assert await _vinculos(db_session_factory) == 0


def test_el_certificado_no_reemplaza_al_token(client, token_nodo):
    assert _autenticar(client, "token-incorrecto", DEVICE_ID) == "deny"


def test_revocar_el_token_corta_tambien_al_nodo_con_certificado(client, token_nodo, token_admin):
    assert _autenticar(client, token_nodo, DEVICE_ID) == "allow"
    client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/revocar",
        json={"motivo": "nodo retirado"},
        headers=auth_header(token_admin),
    )

    assert _autenticar(client, token_nodo, DEVICE_ID) == "deny"


def test_exigencia_global_rechaza_a_todo_nodo_sin_certificado(client, token_nodo, monkeypatch):
    monkeypatch.setenv("BROKER_EXIGIR_CERTIFICADO", "true")
    get_settings.cache_clear()

    assert _autenticar(client, token_nodo) == "deny"
    assert _autenticar(client, token_nodo, DEVICE_ID) == "allow"


def test_cn_desmesurado_se_rechaza(client, token_nodo):
    respuesta = client.post(
        "/api/dispositivos/autenticar",
        json={"device_id": DEVICE_ID, "token": token_nodo, "cert_common_name": "x" * 300},
        headers=CABECERA_BROKER,
    )
    assert respuesta.status_code == 422
