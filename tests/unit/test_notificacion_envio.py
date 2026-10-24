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
from src.infrastructure.notifications import transporte_email
from src.infrastructure.notifications.notificacion_service import NotificacionService
from src.infrastructure.notifications.transporte_email import (
    ErrorEnvioCorreo,
    HttpApiTransporte,
    MensajeCorreo,
    SmtpTransporte,
    construir_transporte,
)


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


_MENSAJE = MensajeCorreo(
    remitente="de@x.pe", destinatario="para@x.pe", asunto="[ColdCare] EXCURSIÓN CRÍTICA — FARM-01", cuerpo="x"
)


# ── HU-53: transportes del correo ────────────────────────────────────────


@pytest.mark.asyncio
async def test_un_fallo_de_smtp_se_informa_al_despachador(monkeypatch):
    """El transporte no traga el error: el despachador necesita saberlo para
    auditar el fallo y reintentar (un aviso que no salió no pasa por enviado)."""
    transporte = SmtpTransporte(_settings(smtp_host="smtp.invalido.test", smtp_user="u", smtp_password="p"))

    def explota(correo):
        raise OSError("no se pudo conectar con el servidor SMTP")

    monkeypatch.setattr(transporte, "_enviar_bloqueante", explota)
    with pytest.raises(ErrorEnvioCorreo, match="OSError"):
        await transporte.enviar(_MENSAJE)


@pytest.mark.asyncio
@pytest.mark.parametrize(("seguridad", "clase_esperada"), [("ssl", "SMTP_SSL"), ("starttls", "SMTP")])
async def test_smtp_usa_ssl_o_starttls_segun_configuracion(monkeypatch, seguridad, clase_esperada):
    usados: list[tuple[str, bool]] = []

    class _Servidor:
        def __init__(self, nombre):
            self.nombre = nombre
            self.tls = False

        def __call__(self, *a, **k):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            self.tls = True

        def login(self, *a):
            pass

        def send_message(self, correo):
            usados.append((self.nombre, self.tls))

    monkeypatch.setattr(transporte_email.smtplib, "SMTP_SSL", _Servidor("SMTP_SSL"))
    monkeypatch.setattr(transporte_email.smtplib, "SMTP", _Servidor("SMTP"))
    transporte = SmtpTransporte(
        _settings(smtp_host="smtp.x.pe", smtp_user="u", smtp_password="p", smtp_seguridad=seguridad)
    )
    await transporte.enviar(_MENSAJE)
    assert usados == [(clase_esperada, seguridad == "starttls")]


@pytest.mark.asyncio
async def test_api_http_envia_en_formato_resend(monkeypatch):
    capturado = {}

    class ClienteOK:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None, **k):
            capturado.update(url=url, headers=headers, json=json)
            return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "AsyncClient", ClienteOK)
    transporte = HttpApiTransporte(_settings(email_api_key="re_123"))
    await transporte.enviar(_MENSAJE)

    assert capturado["url"] == "https://api.resend.com/emails"
    assert capturado["headers"]["Authorization"] == "Bearer re_123"
    assert capturado["json"]["to"] == ["para@x.pe"]
    assert capturado["json"]["subject"] == _MENSAJE.asunto


@pytest.mark.asyncio
async def test_api_http_con_error_del_proveedor_no_filtra_su_respuesta(monkeypatch):
    class Cliente422:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, **k):
            return httpx.Response(422, text="api key re_123 invalid", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "AsyncClient", Cliente422)
    with pytest.raises(ErrorEnvioCorreo) as error:
        await HttpApiTransporte(_settings(email_api_key="re_123")).enviar(_MENSAJE)
    assert str(error.value) == "HTTP 422"


def test_sin_correo_habilitado_no_hay_transporte():
    assert construir_transporte(_settings()) is None
    assert isinstance(construir_transporte(_settings(email_enabled=True)), SmtpTransporte)
    assert isinstance(
        construir_transporte(_settings(email_enabled=True, email_transporte="http_api")), HttpApiTransporte
    )


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
