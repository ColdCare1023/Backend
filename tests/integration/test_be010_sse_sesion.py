"""BE-010: un stream SSE no sobrevive a la sesión que lo abrió (expiración del
token padre, desactivación del usuario, sesiones invalidadas)."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.domain.value_objects.rol import Rol
from src.infrastructure.config import get_settings
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from src.infrastructure.security.jwt_handler import JWTHandler
from src.interface.api.sse_router import sesion_del_ticket_vigente


async def _ticket(crear_usuario, **padre):
    usuario = await crear_usuario("SSE", f"sse-{uuid4().hex[:6]}@farmacia.example.org", "password123", Rol.TECNICO)
    handler = JWTHandler(get_settings())
    ahora = datetime.now(tz=timezone.utc)
    valores = {"token_padre_jti": uuid4().hex, "token_padre_iat": ahora, "token_padre_exp": ahora + timedelta(hours=1)}
    valores.update(padre)
    return usuario, handler.validar_ticket_sse(handler.crear_ticket_sse(usuario.id, **valores))


@pytest.mark.asyncio
async def test_sesion_vigente(crear_usuario, db_session_factory):
    _usuario, ticket = await _ticket(crear_usuario)
    assert await sesion_del_ticket_vigente(db_session_factory, ticket) is True


@pytest.mark.asyncio
async def test_token_padre_caducado_corta_el_stream(crear_usuario, db_session_factory):
    _usuario, ticket = await _ticket(crear_usuario, token_padre_exp=datetime.now(tz=timezone.utc) - timedelta(seconds=1))
    assert await sesion_del_ticket_vigente(db_session_factory, ticket) is False


@pytest.mark.asyncio
async def test_usuario_desactivado_corta_el_stream(crear_usuario, db_session_factory):
    usuario, ticket = await _ticket(crear_usuario)
    async with db_session_factory() as session:
        repo = SQLAlchemyUsuarioRepository(session)
        guardado = await repo.obtener_por_id(usuario.id)
        guardado.is_active = False
        await repo.actualizar(guardado)
        await session.commit()

    assert await sesion_del_ticket_vigente(db_session_factory, ticket) is False


@pytest.mark.asyncio
async def test_sesiones_invalidadas_cortan_el_stream(crear_usuario, db_session_factory):
    usuario, ticket = await _ticket(crear_usuario, token_padre_iat=datetime.now(tz=timezone.utc) - timedelta(minutes=5))
    async with db_session_factory() as session:
        repo = SQLAlchemyUsuarioRepository(session)
        guardado = await repo.obtener_por_id(usuario.id)
        guardado.invalidar_sesiones(datetime.now(tz=timezone.utc))
        await repo.actualizar(guardado)
        await session.commit()

    assert await sesion_del_ticket_vigente(db_session_factory, ticket) is False
