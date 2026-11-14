"""Relé de correo en Google Apps Script y envío transaccional por el transporte común."""

import json
import logging
import smtplib

import httpx
import pytest

from src.infrastructure.config import Settings
from src.infrastructure.notifications import correo_transaccional
from src.infrastructure.notifications.correo_transaccional import EnviadorCorreo
from src.infrastructure.notifications.transporte_email import (
    AppsScriptTransporte,
    ErrorEnvioCorreo,
    HttpApiTransporte,
    MensajeCorreo,
    SmtpTransporte,
    construir_transporte,
)

SECRETO = "secreto-compartido-de-prueba-0123456789"
URL = "https://script.google.com/macros/s/AKfy-prueba/exec"
_MENSAJE = MensajeCorreo(
    remitente="avisos@coldcare.pe", destinatario="ana@x.pe, luis@y.pe", asunto="[ColdCare] Prueba",
    cuerpo="Tu código: 123456", html="<p>123456</p>",
)


def _settings(**extra) -> Settings:
    base = {
        "environment": "test", "_env_file": None, "jwt_secret_key": "x" * 48, "email_enabled": True,
        "email_transporte": "apps_script", "email_api_url": URL, "email_api_key": SECRETO,
        "email_from": "avisos@coldcare.pe",
    }
    base.update(extra)
    return Settings(**base)


def _transporte(manejar, **extra) -> AppsScriptTransporte:
    return AppsScriptTransporte(_settings(**extra), transport=httpx.MockTransport(manejar))


_UNO = MensajeCorreo(
    remitente="avisos@coldcare.pe", destinatario="ana@x.pe", asunto="[ColdCare] Prueba",
    cuerpo="Tu código: 123456", html="<p>123456</p>",
)


@pytest.mark.asyncio
async def test_envia_el_json_del_contrato_un_destinatario_por_peticion():
    recibidas: list[httpx.Request] = []

    def manejar(peticion: httpx.Request) -> httpx.Response:
        recibidas.append(peticion)
        return httpx.Response(200, json={"ok": True})

    await _transporte(manejar).enviar(_MENSAJE)

    # El relé solo acepta un destinatario: dos destinatarios son dos peticiones.
    assert [json.loads(p.content)["to"] for p in recibidas] == [["ana@x.pe"], ["luis@y.pe"]]
    peticion = recibidas[0]
    assert str(peticion.url) == URL and peticion.method == "POST"
    assert json.loads(peticion.content) == {
        "secreto": SECRETO, "to": ["ana@x.pe"], "subject": "[ColdCare] Prueba",
        "text": "Tu código: 123456", "html": "<p>123456</p>", "nombre_remitente": "ColdCare",
    }
    # El secreto viaja en el cuerpo, no en la URL ni en cabeceras.
    assert SECRETO not in str(peticion.url) and SECRETO not in str(peticion.headers)


@pytest.mark.asyncio
async def test_sigue_la_redireccion_de_apps_script_con_get_y_sin_el_secreto():
    vistas: list[httpx.Request] = []

    def manejar(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion)
        if peticion.url.host == "script.google.com":
            return httpx.Response(302, headers={"Location": "https://script.googleusercontent.com/macros/echo?x=1"})
        return httpx.Response(200, json={"ok": True})

    await _transporte(manejar, email_from_nombre="Farmacia Central").enviar(_UNO)

    assert [(v.method, v.url.host) for v in vistas] == [
        ("POST", "script.google.com"), ("GET", "script.googleusercontent.com"),
    ]
    assert vistas[1].content == b"" and SECRETO not in str(vistas[1].url)
    assert json.loads(vistas[0].content)["nombre_remitente"] == "Farmacia Central"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("estado", "destino"),
    [
        # 307/308 conservan el método y el cuerpo: httpx reenviaría el secreto.
        (307, "https://script.googleusercontent.com/macros/echo?x=1"),
        (308, "https://script.googleusercontent.com/macros/echo?x=1"),
        (302, "https://atacante.example/recoger"),
        (302, "http://script.googleusercontent.com/macros/echo?x=1"),
        (301, "https://script.googleusercontent.com/macros/echo?x=1"),
    ],
)
async def test_cualquier_otra_redireccion_es_un_fallo_y_no_se_sigue(estado, destino):
    vistas: list[httpx.Request] = []

    def manejar(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion)
        if len(vistas) == 1:
            return httpx.Response(estado, headers={"Location": destino})
        return httpx.Response(200, json={"ok": True})

    with pytest.raises(ErrorEnvioCorreo) as error:
        await _transporte(manejar).enviar(_UNO)
    assert len(vistas) == 1  # nunca se pidió el destino de la redirección
    assert SECRETO not in str(error.value) and "atacante" not in str(error.value)


@pytest.mark.asyncio
async def test_una_segunda_redireccion_tampoco_se_sigue():
    vistas: list[httpx.Request] = []

    def manejar(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion)
        return httpx.Response(302, headers={"Location": "https://script.googleusercontent.com/otra"})

    with pytest.raises(ErrorEnvioCorreo, match="HTTP 302"):
        await _transporte(manejar).enviar(_UNO)
    assert [v.method for v in vistas] == ["POST", "GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("respuesta", "esperado"),
    [
        (httpx.Response(500, text=f"error con {SECRETO}"), "HTTP 500"),
        (httpx.Response(200, json={"ok": False, "error": f"secreto {SECRETO} invalido"}), "no confirmó"),
        (httpx.Response(200, text="<html>Inicia sesión en Google</html>"), "sin JSON"),
        (httpx.Response(200, json=["ok"]), "no confirmó"),
    ],
)
async def test_solo_200_con_ok_true_es_exito_y_el_error_no_filtra_el_secreto(respuesta, esperado):
    with pytest.raises(ErrorEnvioCorreo) as error:
        await _transporte(lambda _p: respuesta).enviar(_MENSAJE)
    assert esperado in str(error.value)
    assert SECRETO not in str(error.value)


@pytest.mark.asyncio
async def test_un_fallo_de_red_es_error_de_envio():
    def manejar(peticion: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("sin red", request=peticion)

    with pytest.raises(ErrorEnvioCorreo, match="ConnectTimeout"):
        await _transporte(manejar).enviar(_MENSAJE)


@pytest.mark.asyncio
async def test_el_cliente_usa_un_timeout_de_15_segundos():
    from src.infrastructure.notifications import transporte_email

    timeouts: list[dict] = []

    def manejar(peticion: httpx.Request) -> httpx.Response:
        timeouts.append(peticion.extensions["timeout"])
        if peticion.url.host == "script.google.com":
            return httpx.Response(302, headers={"Location": "https://script.googleusercontent.com/macros/echo"})
        return httpx.Response(200, json={"ok": True})

    await _transporte(manejar).enviar(_UNO)

    assert transporte_email.TIMEOUT_APPS_SCRIPT_SEGUNDOS == 15
    assert len(timeouts) == 2
    assert all(set(t.values()) == {15} for t in timeouts)  # connect, read, write y pool


def test_construir_transporte_elige_apps_script_y_conserva_los_demas():
    assert isinstance(construir_transporte(_settings()), AppsScriptTransporte)
    assert isinstance(construir_transporte(_settings(email_transporte="http_api")), HttpApiTransporte)
    assert isinstance(construir_transporte(_settings(email_transporte="smtp")), SmtpTransporte)
    assert construir_transporte(_settings(email_enabled=False)) is None


# ── Envío transaccional (recuperación, códigos) ───────────────────────────


class _Transporte:
    nombre = "doble"

    def __init__(self, falla: bool = False) -> None:
        self.enviados: list[MensajeCorreo] = []
        self._falla = falla

    async def enviar(self, mensaje: MensajeCorreo) -> None:
        if self._falla:
            raise ErrorEnvioCorreo("HTTP 503")
        self.enviados.append(mensaje)


def test_el_enviador_transaccional_usa_el_transporte_comun():
    """Antes solo funcionaba con SMTP_ENABLED y SSL implícito: con http_api o
    apps_script la recuperación respondía 503 aunque el correo estuviera listo."""
    assert isinstance(EnviadorCorreo(_settings())._transporte, AppsScriptTransporte)
    assert EnviadorCorreo(_settings()).configurado
    assert EnviadorCorreo(_settings(email_transporte="http_api", email_api_key="re_x")).configurado
    assert not EnviadorCorreo(_settings(email_enabled=False)).configurado
    assert not EnviadorCorreo(_settings(email_from="")).configurado  # sin remitente no se envía


@pytest.mark.asyncio
async def test_el_enviador_arma_el_mensaje_con_el_remitente_configurado():
    transporte = _Transporte()
    assert await EnviadorCorreo(_settings(), transporte).enviar("ana@x.pe", "Asunto", "Texto", "<p>h</p>")
    (mensaje,) = transporte.enviados
    assert (mensaje.remitente, mensaje.destinatario, mensaje.html) == ("avisos@coldcare.pe", "ana@x.pe", "<p>h</p>")


@pytest.mark.asyncio
async def test_un_fallo_se_informa_con_false_sin_registrar_destinatario_ni_cuerpo(monkeypatch):
    registrados: list[str] = []
    monkeypatch.setattr(
        correo_transaccional.logger, "error", lambda msg, *a, **k: registrados.append(msg % a if a else msg)
    )
    resultado = await EnviadorCorreo(_settings(), _Transporte(falla=True)).enviar(
        "ana@x.pe", "Asunto", "Tu código: 654321"
    )
    assert resultado is False
    assert registrados and all("ana@x.pe" not in r and "654321" not in r for r in registrados)


@pytest.mark.asyncio
async def test_un_error_smtp_no_deja_el_destinatario_en_el_log(monkeypatch):
    """Los errores SMTP citan al destinatario (SMTPRecipientsRefused): del
    transporte "smtp" solo se registra el tipo de la causa."""
    from src.infrastructure.notifications import transporte_email

    class _Servidor:
        def __init__(self, *a, **k) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, *a):
            pass

        def send_message(self, *a):
            raise smtplib.SMTPRecipientsRefused({"ana@x.pe": (550, b"no existe ana@x.pe")})

    monkeypatch.setattr(transporte_email.smtplib, "SMTP_SSL", _Servidor)
    registrados: list[str] = []
    monkeypatch.setattr(
        correo_transaccional.logger, "error", lambda msg, *a, **k: registrados.append(msg % a if a else msg)
    )
    ajustes = _settings(email_transporte="smtp", smtp_host="smtp.x.pe", smtp_user="u", smtp_password="p")

    assert await EnviadorCorreo(ajustes).enviar("ana@x.pe", "Asunto", "Tu código: 654321") is False
    (linea,) = registrados
    assert "SMTPRecipientsRefused" in linea
    assert "ana@x.pe" not in linea and "654321" not in linea


@pytest.mark.asyncio
async def test_smtp_transaccional_respeta_smtp_seguridad(monkeypatch):
    """STARTTLS en 587: antes el envío transaccional forzaba SMTP_SSL y fallaba."""
    from src.infrastructure.notifications import transporte_email

    usados: list[str] = []

    class _Servidor:
        def __init__(self, clase: str) -> None:
            self._clase = clase

        def __call__(self, *a, **k):
            usados.append(self._clase)
            return self

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            usados.append("starttls")

        def login(self, *a):
            pass

        def send_message(self, *a):
            pass

    monkeypatch.setattr(transporte_email.smtplib, "SMTP", _Servidor("SMTP"))
    monkeypatch.setattr(transporte_email.smtplib, "SMTP_SSL", _Servidor("SMTP_SSL"))
    ajustes = _settings(
        email_transporte="smtp", smtp_host="smtp.x.pe", smtp_user="u", smtp_password="p", smtp_port=587,
        smtp_seguridad="starttls",
    )
    assert await EnviadorCorreo(ajustes).enviar("ana@x.pe", "Asunto", "Texto")
    assert usados == ["SMTP", "starttls"]


def test_el_logger_del_modulo_existe():
    assert isinstance(correo_transaccional.logger, logging.Logger)
