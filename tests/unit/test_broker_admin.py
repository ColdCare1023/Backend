"""BE-002: aprovisionamiento, revocación y expulsión de dispositivos vía la API de EMQX."""

import json

import httpx
import pytest

from src.infrastructure.config import Settings
from src.infrastructure.mqtt import broker_admin
from src.infrastructure.mqtt.broker_admin import BrokerAdmin


def _settings(**valores) -> Settings:
    base = {"broker_api_url": "http://emqx:18083/api/v5/", "broker_api_key": "clave", "broker_api_secret": "secreto"}
    base.update(valores)
    return Settings(**base)


@pytest.fixture
def errores_registrados(monkeypatch):
    """Espía de `logger.error` del módulo. No se usa `caplog`: en la suite
    completa otras pruebas alteran el estado global de logging."""
    capturados: list[str] = []
    monkeypatch.setattr(
        broker_admin.logger, "error", lambda mensaje, *a, **k: capturados.append(mensaje % a if a else mensaje)
    )
    return capturados


CLIENTE_INEXISTENTE = {"code": "CLIENTID_NOT_FOUND", "message": "Client ID not found"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "codigo,cuerpo,esperado", [(204, None, True), (404, CLIENTE_INEXISTENTE, True), (500, None, False)]
)
async def test_expulsa_con_delete_autenticado(codigo, cuerpo, esperado):
    vistas: list[httpx.Request] = []

    def manejar(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion)
        return httpx.Response(codigo, json=cuerpo) if cuerpo else httpx.Response(codigo)

    resultado = await BrokerAdmin(_settings(), transport=httpx.MockTransport(manejar)).desconectar_cliente("FARM/01")

    assert resultado is esperado
    assert vistas[0].method == "DELETE"
    # El client_id va codificado: no puede salirse de /clients/{id}.
    assert vistas[0].url.raw_path == b"/api/v5/clients/FARM%2F01"
    assert vistas[0].headers["authorization"].startswith("Basic ")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "respuesta",
    [
        httpx.Response(404, text="<html><body>Not Found</body></html>", headers={"content-type": "text/html"}),
        httpx.Response(404, json={"code": "NOT_FOUND", "message": "Request Path Not Found"}),
        httpx.Response(404),
    ],
    ids=["html", "otro_codigo", "sin_cuerpo"],
)
async def test_404_sin_codigo_de_cliente_no_cuenta_como_expulsado(respuesta, errores_registrados):
    """Con BROKER_API_URL sin /api/v5 cada expulsión respondía 404 y el backend la
    daba por hecha: solo CLIENTID_NOT_FOUND significa que no había sesión."""
    broker = BrokerAdmin(_settings(), transport=httpx.MockTransport(lambda _p: respuesta))

    assert await broker.desconectar_cliente("FARM-01") is False
    assert any("ruta de la API del broker incorrecta" in m for m in errores_registrados)


@pytest.mark.asyncio
async def test_verificar_api_consulta_clients_y_acepta_200():
    vistas: list[httpx.Request] = []

    def manejar(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion)
        return httpx.Response(200, json={"data": [], "meta": {"count": 0}})

    assert await BrokerAdmin(_settings(), transport=httpx.MockTransport(manejar)).verificar_api() is True
    assert vistas[0].method == "GET"
    assert vistas[0].url.raw_path == b"/api/v5/clients"
    assert vistas[0].headers["authorization"].startswith("Basic ")


@pytest.mark.asyncio
async def test_verificar_api_informa_ruta_o_broker_inaccesible(errores_registrados):
    ruta_mala = BrokerAdmin(_settings(), transport=httpx.MockTransport(lambda _p: httpx.Response(404)))
    assert await ruta_mala.verificar_api() is False
    assert any("BROKER_API_URL" in m for m in errores_registrados)

    def sin_red(peticion):
        raise httpx.ConnectError("sin red", request=peticion)

    assert await BrokerAdmin(_settings(), transport=httpx.MockTransport(sin_red)).verificar_api() is False


@pytest.mark.asyncio
async def test_verificar_api_sin_configurar_no_sale_a_la_red():
    def manejar(_peticion):  # pragma: no cover - no debe llamarse
        raise AssertionError("no debía haber petición")

    broker = BrokerAdmin(_settings(broker_api_url=""), transport=httpx.MockTransport(manejar))
    assert await broker.verificar_api() is None


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
