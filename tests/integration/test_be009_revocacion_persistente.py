"""BE-009: la revocación de tokens es persistente y común a todas las
instancias; no depende de la memoria del proceso que atendió el logout."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from src.infrastructure.database.repositories.jti_repository import (
    MOTIVO_TICKET_SSE,
    SQLAlchemyJtiRepository,
)
from src.interface.api import deps
from src.interface.main import create_app
from tests.conftest import auth_header


def _otra_instancia(db_session_factory) -> TestClient:
    """Otra instancia del backend (o la misma tras reiniciar) sobre la misma base."""
    aplicacion = create_app()

    async def override_get_db():
        async with db_session_factory() as session:
            yield session

    aplicacion.dependency_overrides[deps.get_db] = override_get_db
    aplicacion.state.session_factory = db_session_factory
    return TestClient(aplicacion)


def test_token_cerrado_no_revive_en_otra_instancia(client, token_tecnico, db_session_factory):
    assert client.post("/api/auth/logout", headers=auth_header(token_tecnico)).status_code == 204

    with _otra_instancia(db_session_factory) as otra:
        respuesta = otra.get("/api/lecturas", headers=auth_header(token_tecnico))
    assert respuesta.status_code == 401


def test_cambio_de_rol_invalida_las_sesiones_abiertas(client, token_admin, token_tecnico):
    usuario_id = client.get("/api/usuarios", headers=auth_header(token_admin)).json()
    tecnico = next(u for u in usuario_id if u["rol"] == "tecnico")

    assert client.patch(
        f"/api/usuarios/{tecnico['id']}/rol", json={"rol": "farmaceutico"}, headers=auth_header(token_admin)
    ).status_code == 200

    assert client.get("/api/lecturas", headers=auth_header(token_tecnico)).status_code == 401


@pytest.mark.asyncio
async def test_un_ticket_solo_se_consume_una_vez(db_session):
    repo = SQLAlchemyJtiRepository(db_session)
    expira = datetime.now(tz=timezone.utc) + timedelta(seconds=60)

    assert await repo.invalidar("ticket-1", expira, MOTIVO_TICKET_SSE) is True
    assert await repo.invalidar("ticket-1", expira, MOTIVO_TICKET_SSE) is False
    assert await repo.esta_invalidado("ticket-1") is True


@pytest.mark.asyncio
async def test_los_jti_expirados_se_purgan(db_session):
    repo = SQLAlchemyJtiRepository(db_session)
    await repo.invalidar("viejo", datetime.now(tz=timezone.utc) - timedelta(seconds=1), "logout")
    await repo.invalidar("nuevo", datetime.now(tz=timezone.utc) + timedelta(minutes=5), "logout")

    assert await repo.esta_invalidado("viejo") is False
    assert await repo.esta_invalidado("nuevo") is True
