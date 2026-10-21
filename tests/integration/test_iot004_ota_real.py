"""IOT-004: despliegue OTA real.

Con URL de descarga y tamaño, ejecutar un despliegue publica la orden OTA
para el nodo (URL HTTPS, SHA-256, tamaño) y el despliegue queda `enviado`.
Solo el nodo lo da por instalado (`ota_confirmada`) o no (`ota_fallida`,
`ota_rechazada`, `ota_revertida`).
"""

import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.firmware_repository import SQLAlchemyFirmwareRepository
from src.interface import main
from src.interface.api import firmware_router
from tests.conftest import auth_header

HUELLA = "b" * 64
URL = "https://firmware.example.org/coldcare-1.3.0.bin"
DEV = "ESP32_OTA_01"


class _PublicadorFalso:
    def __init__(self, configurado: bool = True, confirma: bool = True) -> None:
        self.configurado = configurado
        self._confirma = confirma
        self.ordenes: list[tuple[str, dict]] = []

    async def publicar(self, device_id: str, orden: dict) -> bool:
        self.ordenes.append((device_id, orden))
        return self._confirma


class _Mensaje:
    def __init__(self, topic: str, payload: str) -> None:
        self.topic = topic
        self.payload = payload.encode("utf-8")


class _Mudo:
    async def publicar(self, *_args, **_kwargs) -> None:
        return None


@pytest_asyncio.fixture
async def dispositivo(db_session_factory, monkeypatch):
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    async with db_session_factory() as session:
        await SQLAlchemyDeviceRepository(session).obtener_o_crear(DEV)  # firmware 1.0.0
        await session.commit()
    return db_session_factory


def _publicador(app, **kwargs) -> _PublicadorFalso:
    falso = _PublicadorFalso(**kwargs)
    app.dependency_overrides[firmware_router.get_publicador_ota] = lambda: falso
    return falso


def _release_y_despliegue(client, token_admin, version: str = "1.3.0", con_url: bool = True) -> str:
    cuerpo = {"version": version, "hash_sha256": HUELLA, "descripcion": f"parche {version}"}
    if con_url:
        cuerpo.update(url_descarga=URL, tamano_bytes=1_100_000)
    assert client.post("/api/firmware/releases", json=cuerpo, headers=auth_header(token_admin)).status_code == 201
    respuesta = client.post(
        "/api/firmware/despliegues", json={"device_id": DEV, "version_objetivo": version}, headers=auth_header(token_admin)
    )
    assert respuesta.status_code == 201
    return respuesta.json()["id"]


async def _evento(event_type: str, detalle: dict) -> None:
    datos = {"schema_version": "1.0", "device_id": DEV, "event_type": event_type, "boot_id": 9,
             "time_quality": "sntp_synced", "uptime_s": 120, "detail": detalle,
             "occurred_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    await main._procesar_evento_mqtt(_Mensaje(f"farmacias/{DEV}/eventos", json.dumps(datos)), _Mudo())


async def _despliegue(fabrica, despliegue_id: str) -> dict:
    import uuid

    async with fabrica() as session:
        return await SQLAlchemyFirmwareRepository(session).obtener_despliegue(uuid.UUID(despliegue_id))


async def _firmware(fabrica) -> str:
    async with fabrica() as session:
        return (await SQLAlchemyDeviceRepository(session).obtener(DEV))["firmware_version"]


async def test_ejecutar_publica_la_orden_verificable_y_queda_enviado(client, app, token_admin, dispositivo):
    publicador = _publicador(app)
    despliegue_id = _release_y_despliegue(client, token_admin)

    respuesta = client.post(f"/api/firmware/despliegues/{despliegue_id}/ejecutar", headers=auth_header(token_admin))

    assert respuesta.status_code == 200
    assert respuesta.json()["estado"] == "enviado"
    assert publicador.ordenes == [
        (DEV, {"device_id": DEV, "version": "1.3.0", "url": URL, "sha256": HUELLA, "tamano": 1_100_000})
    ]
    assert await _firmware(dispositivo) == "1.0.0"  # nada se da por instalado sin el nodo


async def test_sin_mqtt_no_se_envia_nada(client, app, token_admin, dispositivo):
    _publicador(app, configurado=False)
    despliegue_id = _release_y_despliegue(client, token_admin)

    respuesta = client.post(f"/api/firmware/despliegues/{despliegue_id}/ejecutar", headers=auth_header(token_admin))

    assert respuesta.status_code == 503
    assert (await _despliegue(dispositivo, despliegue_id))["estado"] == "programado"


async def test_broker_que_no_confirma_deja_el_despliegue_programado(client, app, token_admin, dispositivo):
    _publicador(app, confirma=False)
    despliegue_id = _release_y_despliegue(client, token_admin)

    respuesta = client.post(f"/api/firmware/despliegues/{despliegue_id}/ejecutar", headers=auth_header(token_admin))

    assert respuesta.status_code == 503
    assert (await _despliegue(dispositivo, despliegue_id))["estado"] == "programado"


def test_url_sin_tamano_se_rechaza(client, token_admin):
    respuesta = client.post(
        "/api/firmware/releases",
        json={"version": "1.4.0", "hash_sha256": HUELLA, "descripcion": "x", "url_descarga": URL},
        headers=auth_header(token_admin),
    )
    assert respuesta.status_code == 409


@pytest.mark.parametrize(
    "url", ["http://firmware.example.org/f.bin", "https://x/f bin", "ftp://x/f.bin"], ids=["http", "espacio", "ftp"]
)
def test_url_de_descarga_debe_ser_https(client, token_admin, url):
    respuesta = client.post(
        "/api/firmware/releases",
        json={"version": "1.4.0", "hash_sha256": HUELLA, "descripcion": "x", "url_descarga": url, "tamano_bytes": 10},
        headers=auth_header(token_admin),
    )
    assert respuesta.status_code == 422


async def test_la_confirmacion_del_nodo_completa_el_despliegue(client, app, token_admin, dispositivo):
    _publicador(app)
    despliegue_id = _release_y_despliegue(client, token_admin)
    client.post(f"/api/firmware/despliegues/{despliegue_id}/ejecutar", headers=auth_header(token_admin))

    await _evento("ota_confirmada", {"firmware_version": "1.3.0", "particion": "app1"})

    despliegue = await _despliegue(dispositivo, despliegue_id)
    assert despliegue["estado"] == "exitoso"
    assert despliegue["completado_en"] is not None
    assert await _firmware(dispositivo) == "1.3.0"


@pytest.mark.parametrize(
    ("event_type", "detalle", "estado"),
    [
        ("ota_fallida", {"version": "1.3.0", "resultado": "huella_distinta"}, "fallido"),
        ("ota_rechazada", {"version": "1.3.0", "resultado": "version_no_posterior"}, "fallido"),
        ("ota_revertida", {"firmware_version": "1.0.0", "particion": "app0"}, "revertido"),
    ],
)
async def test_el_nodo_informa_que_no_se_instalo(client, app, token_admin, dispositivo, event_type, detalle, estado):
    _publicador(app)
    despliegue_id = _release_y_despliegue(client, token_admin)
    client.post(f"/api/firmware/despliegues/{despliegue_id}/ejecutar", headers=auth_header(token_admin))

    await _evento(event_type, detalle)

    assert (await _despliegue(dispositivo, despliegue_id))["estado"] == estado
    assert await _firmware(dispositivo) == "1.0.0"

