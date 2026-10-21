"""BE-021: sin passlib, un usuario con hash bcrypt heredado sigue entrando y
su hash se sustituye por Argon2id en ese mismo login."""

import bcrypt
import pytest

from src.domain.value_objects.rol import Rol
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository

EMAIL = "legado@farmacia.example.org"
PASSWORD = "Legado-bcrypt-2024"


async def _hash_guardado(fabrica) -> str:
    async with fabrica() as session:
        return (await SQLAlchemyUsuarioRepository(session).obtener_por_email(EMAIL)).password_hash


@pytest.mark.asyncio
async def test_login_con_hash_bcrypt_heredado_migra_a_argon2id(client, crear_usuario, db_session_factory):
    usuario = await crear_usuario("Legado", EMAIL, PASSWORD, Rol.TECNICO)
    async with db_session_factory() as session:
        repo = SQLAlchemyUsuarioRepository(session)
        guardado = await repo.obtener_por_id(usuario.id)
        guardado.password_hash = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
        await repo.actualizar(guardado)
        await session.commit()
    assert (await _hash_guardado(db_session_factory)).startswith("$2b$")

    respuesta = client.post("/api/auth/login", data={"username": EMAIL, "password": PASSWORD})

    assert respuesta.status_code == 200
    assert (await _hash_guardado(db_session_factory)).startswith("$argon2id$")
    assert client.post("/api/auth/login", data={"username": EMAIL, "password": PASSWORD}).status_code == 200


@pytest.mark.asyncio
async def test_login_fallido_no_toca_el_hash_heredado(client, crear_usuario, db_session_factory):
    usuario = await crear_usuario("Legado", EMAIL, PASSWORD, Rol.TECNICO)
    heredado = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    async with db_session_factory() as session:
        repo = SQLAlchemyUsuarioRepository(session)
        guardado = await repo.obtener_por_id(usuario.id)
        guardado.password_hash = heredado
        await repo.actualizar(guardado)
        await session.commit()

    respuesta = client.post("/api/auth/login", data={"username": EMAIL, "password": "incorrecta-123"})

    assert respuesta.status_code == 401
    assert await _hash_guardado(db_session_factory) == heredado
