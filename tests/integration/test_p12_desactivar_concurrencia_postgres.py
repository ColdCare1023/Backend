"""P-12: dos (o más) desactivaciones simultáneas de la misma versión sobre
PostgreSQL real (tests/postgres.py). Solo una la desactiva y encadena
MODELO_IA_VERSION_DESACTIVADA; las demás reciben VersionNoActivaError, que la
API responde como 409 `version_no_activa`. SQLite en memoria serializa todo y
no reproduce la carrera."""

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.application.use_cases.gestionar_modelo_ia import DesactivarVersionModeloUseCase, VersionNoActivaError
from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.application.use_cases.verificar_integridad_registro import VerificarIntegridadRegistroUseCase
from src.domain.entities.usuario import Usuario
from src.domain.entities.version_modelo_ia import VersionModeloIA
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.models import ModeloIAVersionModel, TraceabilityRecordModel
from src.infrastructure.database.repositories.modelo_ia_repository import SQLAlchemyModeloIARepository
from src.infrastructure.database.repositories.trazabilidad_repository import SQLAlchemyTrazabilidadRepository
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from tests.postgres import a_asyncpg, base_migrada

N_CONCURRENTES = 6
VERSION = "rf-p12-concurrencia-1.0.0"


@pytest.fixture(scope="module")
def dsn():
    with base_migrada() as url:
        yield url


async def _preparar(fabrica) -> Usuario:
    async with fabrica() as session:
        admin = await SQLAlchemyUsuarioRepository(session).agregar(Usuario(
            nombre="Admin PG", email="admin.pg@farmacia.example.org", password_hash="x" * 60,
            rol=Rol.ADMINISTRADOR,
        ))
        repositorio = SQLAlchemyModeloIARepository(session)
        await repositorio.registrar(VersionModeloIA(
            version=VERSION, model_hash="a" * 64, feature_schema_version="termico_minimo",
            dataset_version="corridas-2026-11", scikit_learn_version="1.9.0", python_version="3.12.13",
            trained_at=datetime(2026, 11, 1, tzinfo=timezone.utc), aprobado_por=admin.id, horizonte_min=30,
        ))
        await repositorio.activar(VERSION, datetime.now(tz=timezone.utc))
        await session.commit()
    return admin


async def _desactivar(fabrica, admin_id, indice: int) -> str:
    async with fabrica() as session:
        try:
            await DesactivarVersionModeloUseCase(
                SQLAlchemyModeloIARepository(session),
                RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session)),
            ).execute(VERSION, admin_id, f"Prueba de concurrencia número {indice}")
        except VersionNoActivaError:
            await session.rollback()
            return "409"
        await session.commit()
        return "200"


def test_desactivaciones_simultaneas_solo_una_tiene_exito(dsn):
    async def flujo():
        motor = create_async_engine(a_asyncpg(dsn), pool_size=N_CONCURRENTES + 2)
        fabrica = async_sessionmaker(motor, expire_on_commit=False)
        try:
            admin = await _preparar(fabrica)
            resultados = await asyncio.gather(
                *[_desactivar(fabrica, admin.id, i) for i in range(N_CONCURRENTES)]
            )
            async with fabrica() as session:
                fila = (await session.execute(
                    select(ModeloIAVersionModel).where(ModeloIAVersionModel.version == VERSION)
                )).scalar_one()
                eventos = (await session.execute(
                    select(TraceabilityRecordModel).where(
                        TraceabilityRecordModel.tipo_evento == "MODELO_IA_VERSION_DESACTIVADA"
                    )
                )).scalars().all()
                verificacion = await VerificarIntegridadRegistroUseCase(
                    SQLAlchemyTrazabilidadRepository(session)
                ).execute(chain_id="SISTEMA")
            return resultados, fila, eventos, verificacion
        finally:
            await motor.dispose()

    resultados, fila, eventos, verificacion = asyncio.run(flujo())

    assert sorted(resultados) == ["200"] + ["409"] * (N_CONCURRENTES - 1)
    assert fila.activa is False and fila.desactivada_en is not None
    assert len(eventos) == 1
    assert verificacion.integra is True
