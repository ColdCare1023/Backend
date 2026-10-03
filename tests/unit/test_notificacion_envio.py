"""HU-23: envío del aviso externo ante excursión crítica.

Los caminos de envío estaban sin cubrir. Importan porque su modo de fallo es el
peor posible: si el envío revienta y la excepción se propaga, tumba el
procesamiento de la lectura que originó la alerta —es decir, la excursión
crítica ni siquiera se registra—; y si se traga en silencio sin dejar rastro,
el responsable cree estar vigilado cuando no lo está.
"""

import httpx
import pytest

from src.infrastructure.config import Settings
from src.infrastructure.notifications import notificacion_service as modulo
from src.infrastructure.notifications.notificacion_service import NotificacionService


@pytest.fixture
def errores_registrados(monkeypatch):
    """Espía del logger del módulo.

    Se usa en vez de `caplog` porque este depende del estado global de logging,
    y en la suite completa otras pruebas lo dejan alterado: el mismo caso pasaba
    aislado y fallaba junto al resto. El espía comprueba lo mismo sin depender
    de nada externo."""
    capturados: list[str] = []
    monkeypatch.setattr(
        modulo.logger, "exception", lambda mensaje, *a, **k: capturados.append(mensaje % a if a else mensaje)
    )
    return capturados

CLAVE = "clave-de-pruebas-conforme-rfc7518-01234"


def _settings(**extra) -> Settings:
    base = {"environment": "test", "jwt_secret_key": CLAVE}
    base.update(extra)
    return Settings(**base)


@pytest.mark.asyncio
async def test_un_fallo_de_smtp_no_propaga_y_queda_en_el_log(monkeypatch, errores_registrados):
    """El servidor de correo caído no puede impedir que la excursión se
    registre: el aviso es secundario respecto al dato."""
    servicio = NotificacionService(
        _settings(
            smtp_enabled=True,
            smtp_host="smtp.invalido.test",
            smtp_user="u",
            smtp_password="p",
            smtp_from="de@x.pe",
            smtp_to="para@x.pe",
        )
    )

    def explota(mensaje):
        raise OSError("no se pudo conectar con el servidor SMTP")

    monkeypatch.setattr(servicio, "_smtp_enviar", explota)

    # No debe propagar: la excursión tiene que registrarse aunque el aviso falle.
    await servicio._enviar_email("FARM-01", "19.9 °C", "2026-07-29T10:34:56Z")

    assert any("No se pudo enviar el aviso por email" in m for m in errores_registrados)


@pytest.mark.asyncio
async def test_el_email_se_envia_con_el_dispositivo_y_la_temperatura(monkeypatch):
    """El cuerpo debe permitir actuar sin abrir el sistema: qué nevera y a
    cuánto está."""
    servicio = NotificacionService(
        _settings(
            smtp_enabled=True,
            smtp_host="smtp.x.pe",
            smtp_user="u",
            smtp_password="p",
            smtp_from="de@x.pe",
            smtp_to="para@x.pe",
        )
    )
    enviados = []
    monkeypatch.setattr(servicio, "_smtp_enviar", lambda mensaje: enviados.append(mensaje))

    await servicio._enviar_email("FARM-01-CDL", "19.9 °C", "2026-07-29T10:34:56Z")

    assert len(enviados) == 1
    cuerpo = enviados[0].get_content()
    assert "FARM-01-CDL" in cuerpo
    assert "19.9 °C" in cuerpo
    assert "2–8 °C" in cuerpo


@pytest.mark.asyncio
async def test_un_fallo_de_telegram_no_propaga_y_queda_en_el_log(monkeypatch, errores_registrados):
    servicio = NotificacionService(
        _settings(telegram_enabled=True, telegram_bot_token="t", telegram_chat_id="c")
    )

    class ClienteQueFalla:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **k):
            raise httpx.ConnectError("red inaccesible")

    monkeypatch.setattr(httpx, "AsyncClient", ClienteQueFalla)

    await servicio._enviar_telegram("FARM-01", "19.9 °C", "2026-07-29T10:34:56Z")

    assert any("No se pudo enviar el aviso por Telegram" in m for m in errores_registrados)


@pytest.mark.asyncio
async def test_una_respuesta_de_error_de_telegram_tambien_se_registra(monkeypatch, errores_registrados):
    """Un 401 por token caducado devuelve respuesta, no excepción de red: sin
    `raise_for_status` el fallo pasaría por envío correcto."""
    servicio = NotificacionService(
        _settings(telegram_enabled=True, telegram_bot_token="t", telegram_chat_id="c")
    )

    class ClienteCon401:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, **k):
            return httpx.Response(401, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "AsyncClient", ClienteCon401)

    await servicio._enviar_telegram("FARM-01", "19.9 °C", "2026-07-29T10:34:56Z")

    assert any("No se pudo enviar el aviso por Telegram" in m for m in errores_registrados)


@pytest.mark.asyncio
async def test_telegram_envia_el_mensaje_al_chat_configurado(monkeypatch):
    servicio = NotificacionService(
        _settings(telegram_enabled=True, telegram_bot_token="TOKEN", telegram_chat_id="CHAT")
    )
    capturado = {}

    class ClienteOK:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, **k):
            capturado["url"] = url
            capturado["json"] = json
            return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "AsyncClient", ClienteOK)

    await servicio._enviar_telegram("FARM-01-CDL", "19.9 °C", "2026-07-29T10:34:56Z")

    assert "TOKEN" in capturado["url"]
    assert capturado["json"]["chat_id"] == "CHAT"
    assert "FARM-01-CDL" in capturado["json"]["text"]
