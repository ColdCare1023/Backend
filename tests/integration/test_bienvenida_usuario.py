"""Al crear una cuenta, el usuario recibe un correo real de bienvenida.

HU-53 (v3.3): el SMS de bienvenida se retiró. `telefono` se sigue aceptando en
el alta por compatibilidad, pero no envía nada ni aparece en la auditoría."""

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


async def test_crear_la_cuenta_envia_el_correo_y_lo_audita(client, app, token_admin, db_session_factory):
    transporte = _Transporte()
    app.dependency_overrides[get_notificador_bienvenida] = lambda: NotificadorBienvenida(_ajustes(), transporte)

    respuesta = _alta(client, token_admin)  # con teléfono: un cliente antiguo
    assert respuesta.status_code == 201
    assert respuesta.json()["telefono"] == "+51••••••777"  # se conserva, enmascarado

    (correo,) = transporte.enviados
    assert correo.destinatario == "ana@prueba.pe" and correo.asunto == "Activa tu cuenta de ColdCare"
    assert "Clave-Segura" not in correo.cuerpo  # nunca la contraseña
    assert "https://coldcare.example" in correo.cuerpo
    (fila,) = await _bienvenidas(db_session_factory)
    assert fila.detalle["correo"] == "enviado"
    assert "sms" not in fila.detalle and "telefono" not in fila.detalle
    assert "ana@prueba.pe" not in str(fila.detalle) and "999888" not in str(fila.detalle)  # enmascarado


async def test_un_proveedor_caido_se_audita_como_fallido_y_la_alta_no_falla(client, app, token_admin, db_session_factory):
    app.dependency_overrides[get_notificador_bienvenida] = lambda: NotificadorBienvenida(_ajustes(), _Transporte(falla=True))

    assert _alta(client, token_admin, telefono=None).status_code == 201

    (fila,) = await _bienvenidas(db_session_factory)
    assert fila.detalle["correo"] == "fallido"


async def test_sin_proveedores_configurados_queda_registrado_como_no_configurado(client, app, token_admin, db_session_factory):
    app.dependency_overrides[get_notificador_bienvenida] = lambda: NotificadorBienvenida(_ajustes(), None)

    assert _alta(client, token_admin).status_code == 201

    (fila,) = await _bienvenidas(db_session_factory)
    assert fila.detalle["correo"] == "no_configurado"


async def test_el_aviso_no_se_envia_si_el_alta_falla(client, app, token_admin):
    transporte = _Transporte()
    app.dependency_overrides[get_notificador_bienvenida] = lambda: NotificadorBienvenida(_ajustes(), transporte)

    assert _alta(client, token_admin).status_code == 201
    assert _alta(client, token_admin).status_code == 409  # correo repetido
    assert len(transporte.enviados) == 1


def test_el_texto_incluye_el_enlace_y_no_la_clave():
    texto = bienvenida.componer_correo(_ajustes(), "Ana", "ana@prueba.pe").cuerpo
    assert "https://coldcare.example" in texto and "contraseña:" not in texto.lower()
