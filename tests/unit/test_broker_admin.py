"""BE-002: aprovisionamiento, revocación y expulsión de dispositivos vía la API de EMQX."""

import json

import httpx
import pytest

from src.infrastructure.config import Settings
from src.infrastructure.mqtt.broker_admin import BrokerAdmin


def _settings(**valores) -> Settings:
    base = {"broker_api_url": "http://emqx:18083/api/v5/", "broker_api_key": "clave", "broker_api_secret": "secreto"}
    base.update(valores)
    return Settings(**base)


@pytest.mark.asyncio
@pytest.mark.parametrize("codigo,esperado", [(204, True), (404, True), (500, False)])
async def test_expulsa_con_delete_autenticado(codigo, esperado):
    vistas: list[httpx.Request] = []

    def manejar(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion)
        return httpx.Response(codigo)

    resultado = await BrokerAdmin(_settings(), transport=httpx.MockTransport(manejar)).desconectar_cliente("FARM/01")

    assert resultado is esperado
    assert vistas[0].method == "DELETE"
    # El client_id va codificado: no puede salirse de /clients/{id}.
    assert vistas[0].url.raw_path == b"/api/v5/clients/FARM%2F01"
    assert vistas[0].headers["authorization"].startswith("Basic ")


@pytest.mark.asyncio
async def test_sin_api_configurada_no_sale_a_la_red():
    def manejar(_peticion):  # pragma: no cover - no debe llamarse
        raise AssertionError("no debía haber petición")

    admin = BrokerAdmin(_settings(broker_api_url=""), transport=httpx.MockTransport(manejar))
    assert admin.configurado is False
    assert await admin.desconectar_cliente("FARM-01") is None


@pytest.mark.asyncio
async def test_broker_inaccesible_se_informa_como_fallo():
    def manejar(peticion):
        raise httpx.ConnectError("sin red", request=peticion)

    assert await BrokerAdmin(_settings(), transport=httpx.MockTransport(manejar)).desconectar_cliente("x") is False


RUTA_USUARIOS = b"/api/v5/authentication/password_based:built_in_database/users"


@pytest.mark.asyncio
async def test_aprovisiona_creando_el_usuario_con_basic_auth():
    vistas: list[httpx.Request] = []

    def manejar(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion)
        return httpx.Response(201, json={"user_id": "FARM/01"})

    resultado = await BrokerAdmin(_settings(), transport=httpx.MockTransport(manejar)).aprovisionar_credencial(
        "FARM/01", "token-secreto"
    )

    assert resultado is True
    assert len(vistas) == 1 and vistas[0].method == "POST"
    assert vistas[0].url.raw_path == RUTA_USUARIOS
    assert json.loads(vistas[0].content) == {"user_id": "FARM/01", "password": "token-secreto"}
    assert vistas[0].headers["authorization"].startswith("Basic ")


@pytest.mark.asyncio
async def test_aprovisiona_reemplaza_la_contrasena_si_el_usuario_ya_existe():
    vistas: list[httpx.Request] = []

    def manejar(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion)
        return httpx.Response(409 if peticion.method == "POST" else 200)

    resultado = await BrokerAdmin(_settings(), transport=httpx.MockTransport(manejar)).aprovisionar_credencial(
        "FARM/01", "token-nuevo"
    )

    assert resultado is True
    assert [v.method for v in vistas] == ["POST", "PUT"]
    # El user_id va codificado: no puede salirse de .../users/{id}.
    assert vistas[1].url.raw_path == RUTA_USUARIOS + b"/FARM%2F01"
    assert json.loads(vistas[1].content) == {"password": "token-nuevo"}


@pytest.mark.asyncio
async def test_aprovisionar_informa_el_rechazo_del_broker():
    broker = BrokerAdmin(_settings(), transport=httpx.MockTransport(lambda _p: httpx.Response(500)))
    assert await broker.aprovisionar_credencial("FARM-01", "t") is False


@pytest.mark.asyncio
@pytest.mark.parametrize("codigo,esperado", [(204, True), (404, True), (500, False)])
async def test_elimina_el_usuario_del_broker(codigo, esperado):
    vistas: list[httpx.Request] = []

    def manejar(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion)
        return httpx.Response(codigo)

    resultado = await BrokerAdmin(_settings(), transport=httpx.MockTransport(manejar)).eliminar_credencial("FARM/01")

    assert resultado is esperado
    assert vistas[0].method == "DELETE"
    assert vistas[0].url.raw_path == RUTA_USUARIOS + b"/FARM%2F01"


@pytest.mark.asyncio
async def test_sin_api_configurada_no_hace_nada_y_lo_informa():
    broker = BrokerAdmin(_settings(broker_api_url=""), transport=httpx.MockTransport(lambda _p: httpx.Response(500)))
    assert await broker.aprovisionar_credencial("FARM-01", "t") is None
    assert await broker.eliminar_credencial("FARM-01") is None
    assert await broker.desconectar_cliente("FARM-01") is None


@pytest.mark.asyncio
async def test_modo_manual_no_toca_los_usuarios_pero_si_expulsa_la_sesion():
    """EMQX Serverless no tiene endpoints de usuarios: en modo manual el backend no
    los llama (queda pendiente en la consola) y solo expulsa la sesión por API."""
    vistas: list[httpx.Request] = []

    def manejar(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion)
        return httpx.Response(204)

    broker = BrokerAdmin(_settings(broker_usuarios_modo="manual"), transport=httpx.MockTransport(manejar))

    assert broker.gestiona_usuarios is False
    assert await broker.aprovisionar_credencial("FARM-01", "t") is None
    assert await broker.eliminar_credencial("FARM-01") is None
    assert vistas == []  # ni una llamada a la API
    assert await broker.desconectar_cliente("FARM-01") is True
    assert [v.method for v in vistas] == ["DELETE"]
    assert vistas[0].url.raw_path == b"/api/v5/clients/FARM-01"
