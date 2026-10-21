"""BE-025: cambio, restablecimiento, recuperación por correo y reactivación.

Toda operación que cambia la contraseña invalida las sesiones previas, y una
contraseña asignada por un administrador obliga a elegir una propia antes de
usar el sistema.
"""

import hashlib
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from src.domain.value_objects.rol import Rol
from src.infrastructure.database.models import AuditLogModel, PasswordResetTokenModel
from src.infrastructure.database.repositories.password_reset_repository import (
    SQLAlchemyPasswordResetRepository,
)
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from src.interface.api import deps
from tests.conftest import auth_header

TECNICO = "tecnico@farmacia.example.org"
PASSWORD = "password123"
NUEVA = "OtraClave-2026x"


def _login(client, email: str, password: str):
    return client.post("/api/auth/login", data={"username": email, "password": password})


def _protegido(client, token: str) -> int:
    return client.get("/api/alertas", headers=auth_header(token)).status_code


def _esperar_al_siguiente_segundo() -> None:
    """`iat` tiene resolución de segundos y un restablecimiento invalida las
    sesiones hasta el segundo SIGUIENTE (BE-009): un login en el mismo
    segundo que el restablecimiento no podría distinguirse de uno anterior."""
    time.sleep(1.01 - datetime.now(tz=timezone.utc).microsecond / 1_000_000)


def _cambiar(client, token: str, actual: str, nueva: str):
    return client.post(
        "/api/auth/cambiar-password",
        json={"password_actual": actual, "password_nueva": nueva},
        headers=auth_header(token),
    )


async def _acciones(fabrica) -> set[str]:
    async with fabrica() as session:
        return {a.accion for a in (await session.execute(select(AuditLogModel))).scalars().all()}


# ── Cambio de contraseña ──────────────────────────────────────────────────


async def test_cambiar_password_invalida_las_sesiones_previas(client, token_tecnico, db_session_factory):
    respuesta = _cambiar(client, token_tecnico, PASSWORD, NUEVA)

    assert respuesta.status_code == 200
    nuevo = respuesta.json()["access_token"]
    assert respuesta.json()["require_password_change"] is False
    assert _protegido(client, token_tecnico) == 401
    assert _protegido(client, nuevo) == 200
    assert _login(client, TECNICO, PASSWORD).status_code == 401
    assert _login(client, TECNICO, NUEVA).status_code == 200
    assert "PASSWORD_CAMBIADA" in await _acciones(db_session_factory)


@pytest.mark.parametrize(
    ("actual", "nueva", "codigo"),
    [
        ("incorrecta-1", NUEVA, 400),
        (PASSWORD, PASSWORD, 400),
        (PASSWORD, "sololetrasaqui", 422),
        (PASSWORD, "corta1", 422),
    ],
    ids=["actual_incorrecta", "misma_contrasena", "sin_digitos", "demasiado_corta"],
)
async def test_cambiar_password_rechaza_datos_invalidos(client, token_tecnico, actual, nueva, codigo):
    assert _cambiar(client, token_tecnico, actual, nueva).status_code == codigo
    assert _protegido(client, token_tecnico) == 200


def test_cambiar_password_requiere_sesion(client):
    assert client.post(
        "/api/auth/cambiar-password", json={"password_actual": PASSWORD, "password_nueva": NUEVA}
    ).status_code == 401


# ── Contraseña inicial asignada por el administrador ─────────────────────


async def test_usuario_creado_por_admin_debe_cambiar_la_contrasena_inicial(client, token_admin):
    alta = client.post(
        "/api/usuarios",
        json={"nombre": "Nueva Persona", "email": "nueva@farmacia.example.org", "password": "Inicial-1234", "rol": "tecnico"},
        headers=auth_header(token_admin),
    )
    assert alta.status_code == 201
    assert alta.json()["debe_cambiar_password"] is True

    login = _login(client, "nueva@farmacia.example.org", "Inicial-1234").json()
    assert login["require_password_change"] is True
    assert _protegido(client, login["access_token"]) == 403

    cambio = _cambiar(client, login["access_token"], "Inicial-1234", NUEVA)
    assert cambio.status_code == 200
    token = cambio.json()["access_token"]
    assert client.post("/api/auth/privacidad/aceptar", headers=auth_header(token)).status_code == 200
    assert _protegido(client, token) == 200


async def test_con_contrasena_temporal_se_puede_cerrar_sesion(client, token_admin, crear_usuario):
    usuario = await crear_usuario("Temporal", "temporal@farmacia.example.org", PASSWORD, Rol.TECNICO)
    temporal = client.post(
        f"/api/usuarios/{usuario.id}/restablecer-password", headers=auth_header(token_admin)
    ).json()["password_temporal"]
    _esperar_al_siguiente_segundo()
    token = _login(client, "temporal@farmacia.example.org", temporal).json()["access_token"]

    assert client.post("/api/auth/logout", headers=auth_header(token)).status_code == 204


# ── Restablecimiento por el administrador ────────────────────────────────


async def test_admin_restablece_y_corta_las_sesiones(client, token_admin, token_tecnico, db_session_factory):
    async with db_session_factory() as session:
        tecnico = await SQLAlchemyUsuarioRepository(session).obtener_por_email(TECNICO)

    respuesta = client.post(f"/api/usuarios/{tecnico.id}/restablecer-password", headers=auth_header(token_admin))

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["usuario"]["debe_cambiar_password"] is True
    assert respuesta.headers["Cache-Control"] == "no-store"
    assert _protegido(client, token_tecnico) == 401
    assert _login(client, TECNICO, PASSWORD).status_code == 401
    login = _login(client, TECNICO, cuerpo["password_temporal"]).json()
    assert login["require_password_change"] is True
    assert {"RESTABLECER_PASSWORD_USUARIO"} <= await _acciones(db_session_factory)


async def test_restablecer_password_reglas(client, token_admin, token_tecnico, db_session_factory):
    async with db_session_factory() as session:
        repo = SQLAlchemyUsuarioRepository(session)
        admin = await repo.obtener_por_email("admin@farmacia.example.org")
        tecnico = await repo.obtener_por_email(TECNICO)

    assert client.post(f"/api/usuarios/{admin.id}/restablecer-password", headers=auth_header(token_admin)).status_code == 409
    assert client.post(f"/api/usuarios/{uuid.uuid4()}/restablecer-password", headers=auth_header(token_admin)).status_code == 404
    assert client.post(f"/api/usuarios/{tecnico.id}/restablecer-password", headers=auth_header(token_tecnico)).status_code == 403


# ── Reactivación ──────────────────────────────────────────────────────────


async def test_reactivar_usuario_desactivado(client, token_admin, crear_usuario, db_session_factory):
    usuario = await crear_usuario("Vuelve", "vuelve@farmacia.example.org", PASSWORD, Rol.TECNICO)
    assert client.patch(
        f"/api/usuarios/{usuario.id}/desactivar", json={"motivo": "renuncia"}, headers=auth_header(token_admin)
    ).status_code == 200
    duplicado = client.post(
        "/api/usuarios",
        json={"nombre": "Vuelve", "email": "vuelve@farmacia.example.org", "password": "Inicial-1234", "rol": "tecnico"},
        headers=auth_header(token_admin),
    )
    assert duplicado.status_code in (400, 409)
    assert "reactív" in duplicado.json()["detail"]

    respuesta = client.post(f"/api/usuarios/{usuario.id}/reactivar", headers=auth_header(token_admin))

    assert respuesta.status_code == 200
    assert respuesta.json()["usuario"]["is_active"] is True
    assert _login(client, "vuelve@farmacia.example.org", PASSWORD).status_code == 401
    login = _login(client, "vuelve@farmacia.example.org", respuesta.json()["password_temporal"])
    assert login.status_code == 200
    assert login.json()["require_password_change"] is True
    assert client.post(f"/api/usuarios/{usuario.id}/reactivar", headers=auth_header(token_admin)).status_code == 409
    assert "REACTIVAR_USUARIO" in await _acciones(db_session_factory)


# ── Recuperación por correo ──────────────────────────────────────────────


class _CorreoFalso:
    def __init__(self, configurado: bool = True) -> None:
        self.configurado = configurado
        self.enviados: list[tuple[str, str, str]] = []

    async def enviar(self, destino: str, asunto: str, cuerpo: str) -> bool:
        self.enviados.append((destino, asunto, cuerpo))
        return True


@pytest.fixture
def correo(app):
    falso = _CorreoFalso()
    app.dependency_overrides[deps.get_enviador_correo] = lambda: falso
    return falso


def _token_del_correo(correo: _CorreoFalso) -> str:
    return re.search(r"#token=(\S+)", correo.enviados[-1][2]).group(1)


def _restablecer(client, token: str, nueva: str = NUEVA):
    return client.post("/api/auth/restablecer-password", json={"token": token, "password_nueva": nueva})


async def test_recuperacion_completa_por_correo(client, token_tecnico, correo, db_session_factory):
    respuesta = client.post("/api/auth/recuperar-password", json={"email": TECNICO})

    assert respuesta.status_code == 202
    assert correo.enviados[0][0] == TECNICO
    token = _token_del_correo(correo)
    assert "http://localhost:5173/restablecer-password#token=" in correo.enviados[0][2]

    assert _restablecer(client, token).status_code == 204
    assert _protegido(client, token_tecnico) == 401
    assert _login(client, TECNICO, NUEVA).status_code == 200
    assert _restablecer(client, token, "Tercera-clave-9").status_code == 400
    assert {"RECUPERACION_PASSWORD_SOLICITADA", "PASSWORD_RESTABLECIDA_CON_ENLACE"} <= await _acciones(
        db_session_factory
    )


async def test_la_respuesta_no_revela_si_el_correo_existe(client, token_tecnico, correo):
    existente = client.post("/api/auth/recuperar-password", json={"email": TECNICO})
    inexistente = client.post("/api/auth/recuperar-password", json={"email": "nadie@farmacia.example.org"})

    assert existente.status_code == inexistente.status_code == 202
    assert existente.json() == inexistente.json()
    assert [destino for destino, _, _ in correo.enviados] == [TECNICO]


async def test_solo_vale_el_ultimo_enlace_y_se_guarda_su_huella(client, token_tecnico, correo, db_session_factory):
    client.post("/api/auth/recuperar-password", json={"email": TECNICO})
    primero = _token_del_correo(correo)
    client.post("/api/auth/recuperar-password", json={"email": TECNICO})
    segundo = _token_del_correo(correo)

    async with db_session_factory() as session:
        huellas = (await session.execute(select(PasswordResetTokenModel.token_hash))).scalars().all()
    assert huellas == [hashlib.sha256(segundo.encode()).hexdigest()]
    assert _restablecer(client, primero).status_code == 400
    assert _restablecer(client, segundo).status_code == 204


async def test_enlace_caducado_se_rechaza(client, token_tecnico, db_session_factory):
    async with db_session_factory() as session:
        tecnico = await SQLAlchemyUsuarioRepository(session).obtener_por_email(TECNICO)
        await SQLAlchemyPasswordResetRepository(session).emitir(
            tecnico.id, "token-caducado-de-prueba-0123456789", datetime.now(tz=timezone.utc) - timedelta(minutes=1)
        )
        await session.commit()

    assert _restablecer(client, "token-caducado-de-prueba-0123456789").status_code == 400
    assert _login(client, TECNICO, PASSWORD).status_code == 200


def test_sin_smtp_la_recuperacion_no_esta_disponible(client, app):
    app.dependency_overrides[deps.get_enviador_correo] = lambda: _CorreoFalso(configurado=False)

    assert client.post("/api/auth/recuperar-password", json={"email": TECNICO}).status_code == 503


async def test_cambiar_password_anula_enlaces_pendientes(client, token_tecnico, correo):
    client.post("/api/auth/recuperar-password", json={"email": TECNICO})
    enlace = _token_del_correo(correo)

    assert _cambiar(client, token_tecnico, PASSWORD, NUEVA).status_code == 200
    assert _restablecer(client, enlace, "Tercera-clave-9").status_code == 400
