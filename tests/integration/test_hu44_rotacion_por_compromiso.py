"""HU-44: rotación por compromiso con la consola del broker pendiente.

En modo manual (EMQX Serverless) el broker sigue aceptando el token anterior
hasta que alguien edite la consola. Si ese token se filtró, quien lo tenga
publica con el mismo `device_id` y el backend no sabe con qué token se
autenticó cada conexión. Mientras la operación siga pendiente, la ingesta
retiene la telemetría SIN acuse: el nodo legítimo la conserva en LittleFS y la
reenvía cuando el administrador confirma la consola. Una rotación preventiva
no corta la ingesta."""

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from src.infrastructure.config import get_settings
from src.infrastructure.database.models import AuditLogModel, ThermalReadingModel
from src.interface import main
from src.interface.api.dispositivos_router import get_broker_admin, get_publicador_desafios
from tests.conftest import auth_header

DEVICE_ID = "FARM-HU44CMP"


class _Espia:
    configurado = True

    def __init__(self) -> None:
        self.acuses: list[dict] = []
        self.desafios: list[dict] = []
        self.expulsados: list[str] = []
        self.resultado: bool | None = None  # None = modo manual

    async def publish(self, topic, payload=None, qos=0, **_kwargs):
        mensaje = json.loads(payload)
        (self.desafios if mensaje.get("tipo") == "desafio_posesion" else self.acuses).append(mensaje)

    async def publicar(self, device_id: str, mensaje: str) -> bool:
        """Publicador de desafíos de la API (confirmar consola)."""
        self.desafios.append(json.loads(mensaje))
        return True

    async def aprovisionar_credencial(self, device_id: str, token: str):
        return self.resultado

    async def eliminar_credencial(self, device_id: str):
        return self.resultado

    async def desconectar_cliente(self, client_id: str) -> bool:
        self.expulsados.append(client_id)
        return True


class _Broadcaster:
    async def publicar(self, *_a, **_k):
        return None


class _Mensaje:
    def __init__(self, sufijo: str, payload: dict) -> None:
        self.topic = f"farmacias/{DEVICE_ID}/{sufijo}"
        self.payload = json.dumps(payload).encode()


@pytest.fixture
def espia(app, monkeypatch, db_session_factory):
    get_settings.cache_clear()
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    doble = _Espia()
    monkeypatch.setattr(main, "BrokerAdmin", lambda _settings: doble)
    app.dependency_overrides[get_broker_admin] = lambda: doble
    app.dependency_overrides[get_publicador_desafios] = lambda: doble
    yield doble
    get_settings.cache_clear()


def _alta_confirmada(client, token_admin) -> None:
    cabecera = auth_header(token_admin)
    assert client.post("/api/dispositivos", json={"device_id": DEVICE_ID}, headers=cabecera).status_code == 201
    assert client.post(f"/api/dispositivos/{DEVICE_ID}/broker/confirmar", json={}, headers=cabecera).status_code == 200


def _rotar(client, token_admin, causa: str) -> dict:
    respuesta = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar",
        json={"motivo": "token visto en un log", "causa": causa},
        headers=auth_header(token_admin),
    )
    assert respuesta.status_code == 200
    return respuesta.json()


def _telemetria(reading_id: str) -> dict:
    return {
        "device_id": DEVICE_ID, "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "temperatura_interna": 4.0, "temperatura_ambiental": 20.0, "humedad_ambiental": 50.0,
        "apertura_refrigerador": False, "estado_conectividad": "online", "reading_id": reading_id,
    }


async def _lecturas(db_session_factory) -> list:
    async with db_session_factory() as session:
        return (
            await session.execute(select(ThermalReadingModel).where(ThermalReadingModel.device_id == DEVICE_ID))
        ).all()


async def _acciones(db_session_factory) -> list[str]:
    async with db_session_factory() as session:
        return list((await session.execute(select(AuditLogModel.accion))).scalars().all())


def _dispositivo(client, token_admin) -> dict:
    lista = client.get("/api/dispositivos", headers=auth_header(token_admin)).json()
    return next(d for d in lista if d["id"] == DEVICE_ID)


async def test_rotacion_preventiva_no_corta_la_ingesta(client, token_admin, espia, db_session_factory):
    _alta_confirmada(client, token_admin)
    respuesta = _rotar(client, token_admin, "preventiva")

    await main._procesar_lectura_mqtt(_Mensaje("lecturas", _telemetria("p1")), _Broadcaster(), espia)

    assert respuesta["telemetria_retenida"] is False
    assert len(await _lecturas(db_session_factory)) == 1


async def test_rotacion_por_compromiso_retiene_la_telemetria_sin_acuse(
    client, token_admin, espia, db_session_factory
):
    _alta_confirmada(client, token_admin)
    respuesta = _rotar(client, token_admin, "compromiso")
    acuses_antes = len(espia.acuses)

    await main._procesar_lectura_mqtt(_Mensaje("lecturas", _telemetria("c1")), _Broadcaster(), espia)

    assert respuesta["telemetria_retenida"] is True
    assert "retiene la telemetría" in respuesta["mensaje_broker"]
    assert _dispositivo(client, token_admin)["rotacion_por_compromiso"] is True
    assert await _lecturas(db_session_factory) == []
    # Sin acuse: el nodo conserva la lectura en LittleFS y la reintenta.
    assert len(espia.acuses) == acuses_antes
    acciones = await _acciones(db_session_factory)
    assert "TELEMETRIA_RETENIDA_ROTACION_COMPROMISO" in acciones
    assert "LECTURA_RETENIDA_ROTACION_COMPROMISO" in acciones


async def test_evento_durante_rotacion_por_compromiso_no_altera_el_estado(
    client, token_admin, espia, db_session_factory
):
    _alta_confirmada(client, token_admin)
    _rotar(client, token_admin, "compromiso")

    evento = {"device_id": DEVICE_ID, "tipo_evento": "lwt_online", "timestamp": datetime.now(tz=timezone.utc).isoformat()}
    await main._procesar_evento_mqtt(_Mensaje("eventos", evento), _Broadcaster())

    acciones = await _acciones(db_session_factory)
    assert "EVENTO_RETENIDO_ROTACION_COMPROMISO" in acciones
    assert "DISPOSITIVO_ONLINE" not in acciones


async def test_confirmar_la_consola_expulsa_la_sesion_pero_sigue_reteniendo(
    client, token_admin, espia, db_session_factory
):
    """Confirmar no basta: el administrador pudo no cambiar la consola. Se
    expulsa la sesión y se envía el desafío de posesión; la telemetría sigue
    retenida hasta que el nodo lo firme con el token nuevo."""
    _alta_confirmada(client, token_admin)
    _rotar(client, token_admin, "compromiso")
    await main._procesar_lectura_mqtt(_Mensaje("lecturas", _telemetria("c2")), _Broadcaster(), espia)
    espia.expulsados.clear()

    confirmado = client.post(
        f"/api/dispositivos/{DEVICE_ID}/broker/confirmar", json={"nota": "contraseña cambiada"},
        headers=auth_header(token_admin),
    )
    await main._procesar_lectura_mqtt(_Mensaje("lecturas", _telemetria("c2")), _Broadcaster(), espia)

    assert confirmado.status_code == 200
    assert confirmado.json()["rotacion_por_compromiso"] is True
    assert confirmado.json()["posesion_pendiente"] is True
    # Cambiar la contraseña no cierra sesiones abiertas: se expulsa al confirmar.
    assert espia.expulsados == [DEVICE_ID]
    assert await _lecturas(db_session_factory) == []
    assert espia.desafios and espia.desafios[-1]["tipo"] == "desafio_posesion"


async def test_en_modo_api_la_rotacion_por_compromiso_no_deja_ventana(
    client, token_admin, espia, db_session_factory
):
    espia.resultado = True  # el broker cambió la contraseña y expulsó la sesión
    assert client.post(
        "/api/dispositivos", json={"device_id": DEVICE_ID}, headers=auth_header(token_admin)
    ).status_code == 201
    respuesta = _rotar(client, token_admin, "compromiso")

    await main._procesar_lectura_mqtt(_Mensaje("lecturas", _telemetria("a1")), _Broadcaster(), espia)

    assert respuesta["telemetria_retenida"] is False
    assert len(await _lecturas(db_session_factory)) == 1


def test_causa_desconocida_es_422(client, token_admin, espia):
    _alta_confirmada(client, token_admin)
    respuesta = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar", json={"motivo": "x", "causa": "otra"},
        headers=auth_header(token_admin),
    )
    assert respuesta.status_code == 422
