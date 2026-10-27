"""HU-44/HU-10 (backlog de 51 HU finales): rotación y revocación de
credenciales MQTT por dispositivo. La autenticación es la integrada de EMQX
(usuario = device_id, contraseña = token): el backend aprovisiona y elimina
la credencial en el broker mediante su API de administración."""

import httpx
import pytest

from src.infrastructure.config import Settings
from src.infrastructure.mqtt.broker_admin import BrokerAdmin
from src.interface.api.dispositivos_router import get_broker_admin
from tests.conftest import auth_header

DEVICE_ID = "FARM-HU44-01"


class _BrokerEspia:
    """Sustituye a BrokerAdmin: registra lo que el backend pide al broker."""

    configurado = True

    def __init__(self) -> None:
        self.aprovisionados: dict[str, str] = {}
        self.eliminados: list[str] = []
        self.expulsados: list[str] = []

    async def aprovisionar_credencial(self, device_id: str, token: str) -> bool:
        self.aprovisionados[device_id] = token
        return True

    async def eliminar_credencial(self, device_id: str) -> bool:
        self.aprovisionados.pop(device_id, None)
        self.eliminados.append(device_id)
        return True

    async def desconectar_cliente(self, client_id: str) -> bool:
        self.expulsados.append(client_id)
        return True


@pytest.fixture
def broker_espia(app):
    espia = _BrokerEspia()
    app.dependency_overrides[get_broker_admin] = lambda: espia
    return espia


def _provisionar(client, token_tecnico, device_id=DEVICE_ID) -> None:
    client.post(
        "/api/lecturas",
        json={
            "device_id": device_id,
            "timestamp": "2026-09-01T12:00:00Z",
            "temperatura_interna": 5.0,
            "temperatura_ambiental": 21.0,
            "humedad_ambiental": 55.0,
            "apertura_refrigerador": False,
            "estado_conectividad": "online",
        },
        headers=auth_header(token_tecnico),
    )


def test_rotar_credencial_devuelve_token_en_claro_una_vez(client, token_tecnico, token_admin):
    _provisionar(client, token_tecnico)

    respuesta = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar",
        json={"motivo": "aprovisionamiento inicial"},
        headers=auth_header(token_admin),
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["device_id"] == DEVICE_ID
    assert len(cuerpo["token"]) > 20


def test_rotar_no_admin_es_rechazado(client, token_tecnico, token_farmaceutico):
    _provisionar(client, token_tecnico)

    respuesta = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar",
        json={"motivo": "intento no autorizado"},
        headers=auth_header(token_farmaceutico),
    )
    assert respuesta.status_code == 403


def test_rotar_dispositivo_inexistente_da_404(client, token_admin):
    respuesta = client.post(
        "/api/dispositivos/NO-EXISTE/credenciales/rotar",
        json={"motivo": "no existe"},
        headers=auth_header(token_admin),
    )
    assert respuesta.status_code == 404


def test_rotacion_y_revocacion_quedan_auditadas_y_encadenadas(client, token_tecnico, token_admin):
    _provisionar(client, token_tecnico)
    client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar",
        json={"motivo": "aprovisionamiento inicial"},
        headers=auth_header(token_admin),
    )
    client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/revocar",
        json={"motivo": "compromiso sospechado"},
        headers=auth_header(token_admin),
    )

    trazabilidad = client.get("/api/trazabilidad", headers=auth_header(token_admin)).json()
    tipos = [r["tipo_evento"] for r in trazabilidad]
    assert "ROTACION_CREDENCIAL_DISPOSITIVO" in tipos
    assert "REVOCACION_CREDENCIAL_DISPOSITIVO" in tipos

    auditoria = client.get("/api/auditoria", headers=auth_header(token_admin)).json()
    acciones = [entrada["accion"] for entrada in auditoria]
    assert "ROTAR_CREDENCIAL_DISPOSITIVO" in acciones
    assert "REVOCAR_CREDENCIAL_DISPOSITIVO" in acciones


def test_rotar_no_afecta_telemetria_historica(client, token_tecnico, token_admin):
    """HU-44 Escenario 2: la telemetría ya persistida permanece intacta."""
    _provisionar(client, token_tecnico)
    antes = client.get(
        "/api/lecturas", params={"device_id": DEVICE_ID}, headers=auth_header(token_tecnico)
    ).json()

    client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar",
        json={"motivo": "rotación de rutina"},
        headers=auth_header(token_admin),
    )

    despues = client.get(
        "/api/lecturas", params={"device_id": DEVICE_ID}, headers=auth_header(token_tecnico)
    ).json()
    assert antes == despues


def test_rotar_aprovisiona_el_token_en_el_broker(client, token_tecnico, token_admin, broker_espia):
    """El token que se entrega una sola vez es el mismo que queda como
    contraseña del usuario `device_id` en la autenticación integrada de EMQX."""
    _provisionar(client, token_tecnico)
    cuerpo = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar",
        json={"motivo": "aprovisionamiento inicial"},
        headers=auth_header(token_admin),
    ).json()
    assert cuerpo["credencial_aprovisionada"] is True
    assert broker_espia.aprovisionados == {DEVICE_ID: cuerpo["token"]}


def test_rotar_reemplaza_la_contrasena_anterior_en_el_broker(client, token_tecnico, token_admin, broker_espia):
    _provisionar(client, token_tecnico)
    primero = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar", json={"motivo": "alta"}, headers=auth_header(token_admin)
    ).json()["token"]
    segundo = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar", json={"motivo": "rotación"}, headers=auth_header(token_admin)
    ).json()["token"]
    assert primero != segundo
    assert broker_espia.aprovisionados == {DEVICE_ID: segundo}


def test_rotar_sin_api_del_broker_informa_que_no_se_aprovisiono(client, token_tecnico, token_admin):
    """Sin API de administración configurada el token no sirve hasta
    aprovisionarlo: la respuesta lo dice en vez de aparentar éxito."""
    _provisionar(client, token_tecnico)
    cuerpo = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar", json={"motivo": "alta"}, headers=auth_header(token_admin)
    ).json()
    assert cuerpo["credencial_aprovisionada"] is None
    assert cuerpo["sesion_broker_cerrada"] is None


def test_revocar_elimina_la_credencial_del_broker(client, token_tecnico, token_admin, broker_espia):
    _provisionar(client, token_tecnico)
    client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar", json={"motivo": "alta"}, headers=auth_header(token_admin)
    )
    revocacion = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/revocar",
        json={"motivo": "token posiblemente comprometido"},
        headers=auth_header(token_admin),
    )
    assert revocacion.status_code == 204
    assert DEVICE_ID not in broker_espia.aprovisionados
    assert broker_espia.eliminados == [DEVICE_ID]
    assert broker_espia.expulsados[-1] == DEVICE_ID


def test_rotar_expulsa_la_sesion_con_el_token_anterior(client, token_tecnico, token_admin, broker_espia):
    _provisionar(client, token_tecnico)
    cuerpo = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar", json={"motivo": "alta"}, headers=auth_header(token_admin)
    ).json()
    assert cuerpo["sesion_broker_cerrada"] is True
    assert broker_espia.expulsados == [DEVICE_ID]


def test_rotar_en_modo_manual_pide_crear_el_usuario_en_la_consola(client, token_tecnico, token_admin, app):
    """EMQX Serverless: el token se emite y se expulsa la sesión por API, pero el
    usuario hay que crearlo a mano; la respuesta lo dice con un mensaje claro."""
    llamadas: list[tuple[str, str]] = []

    def manejar(peticion: httpx.Request) -> httpx.Response:
        llamadas.append((peticion.method, peticion.url.path))
        return httpx.Response(204)

    ajustes = Settings(
        broker_api_url="http://emqx:18083/api/v5", broker_api_key="k", broker_api_secret="s",
        broker_usuarios_modo="manual",
    )
    app.dependency_overrides[get_broker_admin] = lambda: BrokerAdmin(ajustes, transport=httpx.MockTransport(manejar))
    _provisionar(client, token_tecnico)

    cuerpo = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar", json={"motivo": "alta"}, headers=auth_header(token_admin)
    ).json()

    assert cuerpo["credencial_aprovisionada"] is None
    assert DEVICE_ID in cuerpo["mensaje_broker"] and "consola" in cuerpo["mensaje_broker"]
    assert cuerpo["sesion_broker_cerrada"] is True
    assert llamadas == [("DELETE", f"/api/v5/clients/{DEVICE_ID}")]  # nada de /authentication


def test_rotar_con_api_de_usuarios_no_trae_mensaje(client, token_tecnico, token_admin, broker_espia):
    _provisionar(client, token_tecnico)
    cuerpo = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar", json={"motivo": "alta"}, headers=auth_header(token_admin)
    ).json()
    assert cuerpo["credencial_aprovisionada"] is True
    assert cuerpo["mensaje_broker"] is None
