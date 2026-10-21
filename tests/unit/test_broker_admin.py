"""BE-002: expulsión de la sesión MQTT de un dispositivo vía la API de EMQX."""

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
