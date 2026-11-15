"""HU-26 esc. 3: la verificación de una cadena puede ejecutarse (y repetirse)
sin bloquear la ingesta normal.

Contra PostgreSQL real, con la API completa: mientras se verifican la cadena
del dispositivo, llegan lecturas por `POST /api/lecturas`. Todas se confirman,
cada verificación concurrente da la cadena por íntegra y, al repetirla cuando
la ingesta termina, el resultado es el mismo (mismo total y mismo hash final)."""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain.entities.usuario import PRIVACY_POLICY_VERSION, Usuario
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.models import ThermalReadingModel, TraceabilityRecordModel
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from src.infrastructure.security.password_hasher import hash_password
from src.interface.api import deps
from src.interface.main import create_app
from tests.postgres import a_asyncpg, base_migrada

DEVICE = "FARM-HU26-CONC"
N_PREVIAS, N_CONCURRENTES = 3, 6


@pytest.fixture(scope="module")
def dsn():
    with base_migrada() as url:
        yield url


async def _usuario(fabrica, email: str, rol: Rol) -> None:
    async with fabrica() as session:
        await SQLAlchemyUsuarioRepository(session).agregar(
            Usuario(
                nombre=email, email=email, password_hash=hash_password("password123"), rol=rol,
                privacy_accepted=True, privacy_version_accepted=PRIVACY_POLICY_VERSION,
            )
        )
        await session.commit()


async def _token(cliente: httpx.AsyncClient, email: str) -> dict:
    respuesta = await cliente.post("/api/auth/login", data={"username": email, "password": "password123"})
    assert respuesta.status_code == 200, respuesta.text
    return {"Authorization": f"Bearer {respuesta.json()['access_token']}"}


def _lectura(minutos_atras: float) -> dict:
    return {
        "device_id": DEVICE,
        "timestamp": (datetime.now(tz=timezone.utc) - timedelta(minutes=minutos_atras)).isoformat(),
        "temperatura_interna": 4.5, "temperatura_ambiental": 21.0, "humedad_ambiental": 50.0,
        "apertura_refrigerador": False, "estado_conectividad": "online",
    }


def test_verificar_en_paralelo_con_la_ingesta_no_la_bloquea_y_se_repite(dsn):
    async def flujo():
        motor = create_async_engine(a_asyncpg(dsn), pool_size=N_CONCURRENTES + 6)
        fabrica = async_sessionmaker(motor, expire_on_commit=False)
        app = create_app()

        async def get_db_postgres():
            async with fabrica() as session:
                yield session

        app.dependency_overrides[deps.get_db] = get_db_postgres
        app.state.session_factory = fabrica
        try:
            await _usuario(fabrica, "tecnico.hu26@farmacia.example.org", Rol.TECNICO)
            await _usuario(fabrica, "auditor.hu26@farmacia.example.org", Rol.AUDITOR)
            await _usuario(fabrica, "qf.hu26@farmacia.example.org", Rol.FARMACEUTICO)
            transporte = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transporte, base_url="http://testserver") as cliente:
                tecnico = await _token(cliente, "tecnico.hu26@farmacia.example.org")
                auditor = await _token(cliente, "auditor.hu26@farmacia.example.org")
                qf = await _token(cliente, "qf.hu26@farmacia.example.org")

                for i in range(N_PREVIAS):
                    assert (await cliente.post("/api/lecturas", json=_lectura(30 - i), headers=tecnico)).status_code == 201

                async def verificar(cabecera):
                    return await cliente.get("/api/trazabilidad/verificar", params={"chain_id": DEVICE}, headers=cabecera)

                inicial = await verificar(auditor)
                concurrentes = await asyncio.gather(
                    *(cliente.post("/api/lecturas", json=_lectura(20 - i), headers=tecnico) for i in range(N_CONCURRENTES)),
                    verificar(auditor),
                    verificar(auditor),
                )
                final, repetida = await verificar(qf), await verificar(qf)

            async with fabrica() as session:
                lecturas = await session.scalar(
                    select(func.count()).select_from(ThermalReadingModel).where(ThermalReadingModel.device_id == DEVICE)
                )
                eslabones = await session.scalar(
                    select(func.count()).select_from(TraceabilityRecordModel)
                    .where(TraceabilityRecordModel.chain_id == DEVICE)
                )
            return inicial, concurrentes, final, repetida, lecturas, eslabones
        finally:
            await motor.dispose()

    inicial, concurrentes, final, repetida, lecturas, eslabones = asyncio.run(flujo())

    posts, verificaciones = concurrentes[:N_CONCURRENTES], concurrentes[N_CONCURRENTES:]
    assert [r.status_code for r in posts] == [201] * N_CONCURRENTES
    assert lecturas == N_PREVIAS + N_CONCURRENTES

    assert inicial.status_code == 200 and inicial.json()["integra"] is True
    for respuesta in verificaciones:
        assert respuesta.status_code == 200, respuesta.text
        cuerpo = respuesta.json()
        assert cuerpo["integra"] is True and cuerpo["estado"] == "integra"
        assert inicial.json()["total_registros"] <= cuerpo["total_registros"] <= eslabones

    assert final.status_code == repetida.status_code == 200
    final, repetida = final.json(), repetida.json()
    assert final["integra"] is True and final["estado"] == "integra"
    assert final["total_registros"] == eslabones
    assert (repetida["integra"], repetida["total_registros"], repetida["hash_final"]) == (
        final["integra"], final["total_registros"], final["hash_final"],
    )
