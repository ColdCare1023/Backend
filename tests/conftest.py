import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MQTT_ENABLED", "false")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
# El registro estricto de dispositivos se prueba explícitamente en
# test_seguridad_api (vía override de settings); el resto de la suite
# ingesta lecturas de devices ad-hoc con auto-registro.
os.environ.setdefault("DEVICE_REGISTRY_ESTRICTO", "false")

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.domain.entities.usuario import Usuario
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.base import Base
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from src.infrastructure.security.password_hasher import hash_password
from src.interface.api import deps
from src.interface.main import create_app


@pytest_asyncio.fixture
async def db_session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield session_factory

    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_session_factory) -> AsyncSession:
    async with db_session_factory() as session:
        yield session


@pytest.fixture
def app(db_session_factory):
    application = create_app()

    async def override_get_db():
        async with db_session_factory() as session:
            yield session

    application.dependency_overrides[deps.get_db] = override_get_db
    return application


@pytest_asyncio.fixture
async def crear_usuario(db_session_factory):
    async def _crear(nombre: str, email: str, password: str, rol: Rol) -> Usuario:
        async with db_session_factory() as session:
            repo = SQLAlchemyUsuarioRepository(session)
            usuario = Usuario(nombre=nombre, email=email, password_hash=hash_password(password), rol=rol, privacy_accepted=True)
            creado = await repo.agregar(usuario)
            await session.commit()
            return creado

    return _crear


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest_asyncio.fixture
async def token_admin(crear_usuario, client):
    await crear_usuario("Admin Test", "admin@farmacia.example.org", "password123", Rol.ADMINISTRADOR)
    response = client.post(
        "/api/auth/login", data={"username": "admin@farmacia.example.org", "password": "password123"}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


@pytest_asyncio.fixture
async def token_farmaceutico(crear_usuario, client):
    await crear_usuario("Farmaceutico Test", "farmaceutico@farmacia.example.org", "password123", Rol.FARMACEUTICO)
    response = client.post(
        "/api/auth/login",
        data={"username": "farmaceutico@farmacia.example.org", "password": "password123"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


@pytest_asyncio.fixture
async def token_tecnico(crear_usuario, client):
    await crear_usuario("Tecnico Test", "tecnico@farmacia.example.org", "password123", Rol.TECNICO)
    response = client.post(
        "/api/auth/login", data={"username": "tecnico@farmacia.example.org", "password": "password123"}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


@pytest_asyncio.fixture
async def token_auditor(crear_usuario, client):
    """HU-41: rol Responsable de auditoría."""
    await crear_usuario("Auditor Test", "auditor@farmacia.example.org", "password123", Rol.AUDITOR)
    response = client.post(
        "/api/auth/login", data={"username": "auditor@farmacia.example.org", "password": "password123"}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
