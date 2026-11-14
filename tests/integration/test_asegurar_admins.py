"""scripts.asegurar_admins: administradores de arranque idempotentes y con activación por código."""

import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from scripts import asegurar_admins
from scripts.asegurar_admins import ACTUALIZADO, CREADO, REACTIVADO, SIN_CAMBIOS, Admin, asegurar
from src.domain.value_objects.rol import Rol
from src.infrastructure.config import Settings
from src.infrastructure.database.models import (
    AuditLogModel,
    CodigoVerificacionModel,
    PasswordResetTokenModel,
    UserModel,
)
from src.infrastructure.database.repositories.password_reset_repository import (
    SQLAlchemyPasswordResetRepository,
)
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from src.infrastructure.notifications.bienvenida import ENVIADO, NO_CONFIGURADO, NotificadorBienvenida
from src.infrastructure.security.password_hasher import verify_password_async
from tests.conftest import auth_header


class _Transporte:
    nombre = "doble"

    def __init__(self) -> None:
        self.enviados = []

    async def enviar(self, mensaje):
        self.enviados.append(mensaje)


def _ajustes() -> Settings:
    return Settings(
        environment="test", _env_file=None, jwt_secret_key="x" * 48, frontend_url="https://coldcare.example",
        email_from="coldcare@prueba.pe",
    )


async def _usuarios(fabrica) -> list[UserModel]:
    async with fabrica() as session:
        return list((await session.execute(select(UserModel))).scalars().all())


async def test_crea_administradores_activos_sin_password_conocida(db_session_factory, capsys):
    transporte = _Transporte()
    notificador = NotificadorBienvenida(_ajustes(), transporte)
    admins = [Admin("ana@farmacia.pe", "Ana Pérez"), Admin("luis@farmacia.pe", "Luis Soto")]

    resultados = await asegurar(admins, db_session_factory, _ajustes(), notificador)

    assert [(r.estado, r.correo) for r in resultados] == [(CREADO, ENVIADO), (CREADO, ENVIADO)]
    assert [r.email_enmascarado for r in resultados] == ["a***@farmacia.pe", "l***@farmacia.pe"]
    async with db_session_factory() as session:
        repo = SQLAlchemyUsuarioRepository(session)
        for admin in admins:
            usuario = await repo.obtener_por_email(admin.email)
            assert usuario.rol == Rol.ADMINISTRADOR and usuario.is_active
            assert usuario.debe_cambiar_password is False
            assert usuario.password_hash.startswith("$argon2")
        codigos = (await session.execute(select(CodigoVerificacionModel))).scalars().all()
    assert sorted(c.proposito for c in codigos) == ["activacion", "activacion"]
    assert [m.destinatario for m in transporte.enviados] == ["ana@farmacia.pe", "luis@farmacia.pe"]
    assert "modo=activar" in transporte.enviados[0].cuerpo
    assert re.search(r"^\s+\d{6}$", transporte.enviados[0].cuerpo, re.MULTILINE)


async def _desactivar(fabrica, usuario_id) -> None:
    async with fabrica() as session:
        modelo = await session.get(UserModel, usuario_id)
        modelo.is_active = False
        modelo.anonymized_for_gdpr = True
        modelo.motivo_desactivacion = "renuncia"
        await session.commit()


async def test_es_idempotente_y_promueve_o_reactiva(db_session_factory, crear_usuario, client):
    await crear_usuario("Tec", "tec@farmacia.pe", "password123", Rol.TECNICO)
    inactivo = await crear_usuario("Baja", "baja@farmacia.pe", "password123", Rol.ADMINISTRADOR)
    await _desactivar(db_session_factory, inactivo.id)
    token_tec = client.post("/api/auth/login", data={"username": "tec@farmacia.pe", "password": "password123"})
    token_tec = token_tec.json()["access_token"]
    assert client.get("/api/alertas", headers=auth_header(token_tec)).status_code == 200

    admins = [Admin("TEC@farmacia.pe", "Tec"), Admin("baja@farmacia.pe", "Baja"), Admin("nueva@farmacia.pe", "Nueva")]
    primera = await asegurar(admins, db_session_factory, _ajustes(), NotificadorBienvenida(_ajustes(), _Transporte()))
    assert [r.estado for r in primera] == [ACTUALIZADO, REACTIVADO, CREADO]
    assert not any(r.sin_password for r in primera)  # todas recibieron su código

    # La promoción cambia el rol y corta la sesión que viajaba con el rol anterior.
    async with db_session_factory() as session:
        repo = SQLAlchemyUsuarioRepository(session)
        for admin in admins:
            assert (await repo.obtener_por_email(admin.email)).rol == Rol.ADMINISTRADOR
        tec = await repo.obtener_por_email("tec@farmacia.pe")
    assert tec.tokens_invalidos_antes_de is not None
    assert client.get("/api/alertas", headers=auth_header(token_tec)).status_code == 401
    # La promoción conserva la contraseña de su titular.
    assert client.post("/api/auth/login", data={"username": "tec@farmacia.pe", "password": "password123"}).status_code == 200

    hash_antes = {u.email: u.password_hash for u in await _usuarios(db_session_factory)}
    segunda = await asegurar(admins, db_session_factory, _ajustes(), NotificadorBienvenida(_ajustes(), _Transporte()))
    assert [r.estado for r in segunda] == [SIN_CAMBIOS, SIN_CAMBIOS, SIN_CAMBIOS]

    usuarios = await _usuarios(db_session_factory)
    assert len(usuarios) == 3  # no duplica (tampoco por mayúsculas)
    assert all(u.is_active and not u.anonymized_for_gdpr and u.motivo_desactivacion is None for u in usuarios)
    assert {u.email: u.password_hash for u in usuarios} == hash_antes  # no toca contraseñas existentes
    async with db_session_factory() as session:
        codigos = (await session.execute(select(CodigoVerificacionModel))).scalars().all()
        auditoria = (await session.execute(select(AuditLogModel))).scalars().all()
    # Un solo código vivo por cuenta: el nuevo anula (y reemplaza) al anterior.
    assert len([c for c in codigos if c.usado_en is None]) == 3
    assert {a.accion for a in auditoria} >= {"ASEGURAR_ADMINISTRADOR"}
    asegurar_admin = [a for a in auditoria if a.accion == "ASEGURAR_ADMINISTRADOR"]
    assert all("@farmacia.pe" not in a.detalle["email"].split("***")[0] for a in asegurar_admin)


async def test_reactivar_no_devuelve_la_contrasena_anterior(db_session_factory, crear_usuario, client):
    """Una cuenta dada de baja (p. ej. por una contraseña filtrada) vuelve sin
    esa contraseña: con el OTP apagado bastaría ella sola para entrar como
    administrador. También se anulan sus enlaces de recuperación pendientes."""
    baja = await crear_usuario("Baja", "baja@farmacia.pe", "password123", Rol.TECNICO)
    async with db_session_factory() as session:
        await SQLAlchemyPasswordResetRepository(session).emitir(
            baja.id, "token-pendiente-de-prueba-0123456789", datetime.now(tz=timezone.utc) + timedelta(minutes=30)
        )
        await session.commit()
    await _desactivar(db_session_factory, baja.id)

    (resultado,) = await asegurar(
        [Admin("baja@farmacia.pe", "Baja")], db_session_factory, _ajustes(), NotificadorBienvenida(_ajustes(), _Transporte())
    )

    assert (resultado.estado, resultado.correo, resultado.sin_password) == (REACTIVADO, ENVIADO, False)
    async with db_session_factory() as session:
        usuario = await SQLAlchemyUsuarioRepository(session).obtener_por_email("baja@farmacia.pe")
        enlaces = (await session.execute(select(PasswordResetTokenModel))).scalars().all()
    assert usuario.is_active and usuario.rol == Rol.ADMINISTRADOR and not usuario.debe_cambiar_password
    assert not await verify_password_async("password123", usuario.password_hash)
    assert usuario.tokens_invalidos_antes_de is not None
    assert enlaces == []  # el enlace pendiente quedó anulado
    login = client.post("/api/auth/login", data={"username": "baja@farmacia.pe", "password": "password123"})
    assert login.status_code == 401


async def test_reactivar_sin_correo_avisa_que_no_hay_contrasena_utilizable(db_session_factory, crear_usuario):
    baja = await crear_usuario("Baja", "baja@farmacia.pe", "password123", Rol.TECNICO)
    await _desactivar(db_session_factory, baja.id)

    (resultado,) = await asegurar([Admin("baja@farmacia.pe", "Baja")], db_session_factory, _ajustes(), enviar_correo=False)

    assert resultado.sin_password and "no tiene contraseña utilizable" in resultado.linea()
    assert "baja@farmacia.pe" not in resultado.linea()


async def test_sin_correo_crea_igual_pero_no_emite_codigo(db_session_factory):
    ajustes = Settings(environment="test", _env_file=None, jwt_secret_key="x" * 48)
    resultados = await asegurar([Admin("ana@farmacia.pe", "Ana")], db_session_factory, ajustes)

    assert [(r.estado, r.correo) for r in resultados] == [(CREADO, NO_CONFIGURADO)]
    async with db_session_factory() as session:
        assert (await session.execute(select(CodigoVerificacionModel))).scalars().all() == []


def test_valida_los_argumentos_sin_imprimir_correos_completos():
    with pytest.raises(SystemExit, match="su --nombre"):
        asegurar_admins._validar(["a@x.pe"], [])
    with pytest.raises(SystemExit) as error:
        asegurar_admins._validar(["no-es-correo"], ["X"])
    assert "no-es-correo" not in str(error.value)
    with pytest.raises(SystemExit, match="repetido"):
        asegurar_admins._validar(["a@x.pe", "A@x.pe"], ["A", "B"])
    assert asegurar_admins._validar([" Ana@X.pe "], [" Ana "]) == [Admin("ana@x.pe", "Ana")]


async def test_la_cli_imprime_solo_correos_enmascarados(monkeypatch, db_session_factory, capsys):
    from src.infrastructure.database import session as modulo_sesion

    monkeypatch.setattr(modulo_sesion, "_session_factory", db_session_factory)
    codigo = await asegurar_admins._principal(
        ["--email", "ana@farmacia.pe", "--nombre", "Ana", "--sin-correo"]
    )

    salida = capsys.readouterr().out
    assert codigo == 0
    assert "a***@farmacia.pe: cuenta creado" in salida and "ana@farmacia.pe" not in salida
    assert not re.search(r"\$argon2|password", salida, re.IGNORECASE)
