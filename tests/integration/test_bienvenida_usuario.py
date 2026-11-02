"""Al crear una cuenta, el usuario recibe un correo y un SMS reales de bienvenida."""

import pytest
from sqlalchemy import select

from src.infrastructure.config import Settings
from src.infrastructure.database.models import AuditLogModel
from src.infrastructure.notifications import bienvenida
from src.infrastructure.notifications.bienvenida import NotificadorBienvenida
from src.infrastructure.notifications.transporte_email import ErrorEnvioCorreo
from src.interface.api.usuarios_router import get_notificador_bienvenida
from tests.conftest import auth_header


class _Transporte:
    nombre = "doble"

    def __init__(self, falla: bool = False) -> None:
        self.enviados = []
        self._falla = falla

    async def enviar(self, mensaje):
        if self._falla:
            raise ErrorEnvioCorreo("proveedor caído")
        self.enviados.append(mensaje)


def _ajustes(**extra) -> Settings:
    return Settings(
        environment="test", _env_file=None, jwt_secret_key="x" * 48, frontend_url="https://coldcare.example",
        email_from="coldcare@prueba.pe", **extra,
    )


def _alta(client, token_admin, telefono="+51999888777"):
    cuerpo = {"nombre": "Ana Prueba", "email": "ana@prueba.pe", "password": "Clave-Segura-2026!x", "rol": "farmaceutico"}
    if telefono:
        cuerpo["telefono"] = telefono
    return client.post("/api/usuarios", json=cuerpo, headers=auth_header(token_admin))


async def _bienvenidas(db_session_factory):
    async with db_session_factory() as session:
        filas = (await session.execute(select(AuditLogModel))).scalars().all()
    return [f for f in filas if f.accion == "BIENVENIDA_NOTIFICADA"]


async def test_crear_la_cuenta_envia_correo_y_sms_y_lo_audita(client, app, token_admin, db_session_factory, monkeypatch):
    transporte = _Transporte()
    sms = []

    class _Sms:
        async def enviar_sms_a(self, telefono, texto, referencia):
            sms.append((telefono, texto))
            return True

    monkeypatch.setattr(bienvenida, "obtener_notificacion_service", lambda s: _Sms())
    ajustes = _ajustes(sms_enabled=True, sms_account_sid="AC1", sms_auth_token="t", sms_from="+15550001111")
    app.dependency_overrides[get_notificador_bienvenida] = lambda: NotificadorBienvenida(ajustes, transporte)

    assert _alta(client, token_admin).status_code == 201

    (correo,) = transporte.enviados
    assert correo.destinatario == "ana@prueba.pe" and correo.asunto == "[ColdCare] Tu cuenta fue creada"
    assert "Clave-Segura" not in correo.cuerpo  # nunca la contraseña
    assert "https://coldcare.example" in correo.cuerpo
    assert sms[0][0] == "+51999888777" and "Clave-Segura" not in sms[0][1]
    (fila,) = await _bienvenidas(db_session_factory)
    assert (fila.detalle["correo"], fila.detalle["sms"]) == ("enviado", "enviado")
    assert "ana@prueba.pe" not in str(fila.detalle) and "999888" not in str(fila.detalle)  # enmascarado


async def test_un_proveedor_caido_se_audita_como_fallido_y_la_alta_no_falla(client, app, token_admin, db_session_factory):
    app.dependency_overrides[get_notificador_bienvenida] = lambda: NotificadorBienvenida(_ajustes(), _Transporte(falla=True))

    assert _alta(client, token_admin, telefono=None).status_code == 201

    (fila,) = await _bienvenidas(db_session_factory)
    assert (fila.detalle["correo"], fila.detalle["sms"]) == ("fallido", "sin_telefono")


async def test_sin_proveedores_configurados_queda_registrado_como_no_configurado(client, app, token_admin, db_session_factory):
    app.dependency_overrides[get_notificador_bienvenida] = lambda: NotificadorBienvenida(_ajustes(), None)

    assert _alta(client, token_admin).status_code == 201

    (fila,) = await _bienvenidas(db_session_factory)
    assert (fila.detalle["correo"], fila.detalle["sms"]) == ("no_configurado", "no_configurado")


async def test_el_aviso_no_se_envia_si_el_alta_falla(client, app, token_admin):
    transporte = _Transporte()
    app.dependency_overrides[get_notificador_bienvenida] = lambda: NotificadorBienvenida(_ajustes(), transporte)

    assert _alta(client, token_admin).status_code == 201
    assert _alta(client, token_admin).status_code == 409  # correo repetido
    assert len(transporte.enviados) == 1


@pytest.mark.parametrize("campo", ["correo", "sms"])
def test_el_texto_incluye_el_enlace_y_no_la_clave(campo):
    ajustes = _ajustes()
    texto = (
        bienvenida.componer_correo(ajustes, "Ana", "ana@prueba.pe").cuerpo
        if campo == "correo" else bienvenida.componer_sms(ajustes)
    )
    assert "https://coldcare.example" in texto and "contraseña:" not in texto.lower()
