"""B-9 (HU-11, HU-22, HU-25): idempotencia y concurrencia sobre PostgreSQL.

SQLite en memoria serializa todo y no reproduce lo que el Paper afirma: que dos
entregas simultáneas de la misma lectura (reenvío QoS 1, dos consumidores)
dejan UNA fila y UN eslabón, y que escrituras concurrentes no bifurcan la
cadena. Aquí corren contra PostgreSQL real (tests/postgres.py)."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.application.use_cases.registrar_lectura_termica import RegistrarLecturaTermicaUseCase
from src.application.use_cases.verificar_integridad_registro import VerificarIntegridadRegistroUseCase
from src.domain.entities.lectura_termica import LecturaTermica
from src.infrastructure.ai.random_forest_service import get_random_forest_service
from src.infrastructure.database.models import ThermalReadingModel, TraceabilityRecordModel
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.trazabilidad_repository import SQLAlchemyTrazabilidadRepository
from tests.postgres import a_asyncpg, base_migrada

N_CONCURRENTES = 8


@pytest.fixture(scope="module")
def dsn():
    with base_migrada() as url:
        yield url


async def _con_fabrica(dsn: str, corrutina):
    motor = create_async_engine(a_asyncpg(dsn), pool_size=N_CONCURRENTES + 2)
    try:
        return await corrutina(async_sessionmaker(motor, expire_on_commit=False))
    finally:
        await motor.dispose()


def _lectura(device_id: str, seq_no: int, temperatura: float = 5.0) -> LecturaTermica:
    return LecturaTermica(
        device_id=device_id, timestamp=datetime.now(tz=timezone.utc) - timedelta(minutes=1),
        temperatura_ambiental=21.0, humedad_ambiental=50.0, temperatura_interna=temperatura,
        apertura_refrigerador=False, estado_conectividad="online", boot_id=11, seq_no=seq_no,
        time_quality="synced",
    )


async def _registrar(fabrica, lectura: LecturaTermica) -> None:
    async with fabrica() as session:
        await RegistrarLecturaTermicaUseCase(
            SQLAlchemyLecturaRepository(session), SQLAlchemyAlertaRepository(session),
            SQLAlchemyTrazabilidadRepository(session), ClasificarRiesgoTermicoUseCase(get_random_forest_service()),
            device_repository=SQLAlchemyDeviceRepository(session),
        ).execute(lectura)
        await session.commit()


def test_entregas_simultaneas_de_la_misma_lectura_dejan_una_fila_y_un_eslabon(dsn):
    async def flujo(fabrica):
        async with fabrica() as session:
            await SQLAlchemyDeviceRepository(session).obtener_o_crear("PG-IDEM-01")
            await session.commit()
        await asyncio.gather(*[_registrar(fabrica, _lectura("PG-IDEM-01", 1)) for _ in range(N_CONCURRENTES)])
        async with fabrica() as session:
            filas = (await session.execute(
                select(func.count()).select_from(ThermalReadingModel).where(ThermalReadingModel.device_id == "PG-IDEM-01")
            )).scalar_one()
            eslabones = (await session.execute(
                select(func.count()).select_from(TraceabilityRecordModel).where(
                    TraceabilityRecordModel.device_id == "PG-IDEM-01",
                    TraceabilityRecordModel.tipo_evento == "LECTURA_TERMICA",
                )
            )).scalar_one()
        return filas, eslabones

    assert asyncio.run(_con_fabrica(dsn, flujo)) == (1, 1)


def test_lecturas_distintas_concurrentes_no_bifurcan_la_cadena(dsn):
    async def flujo(fabrica):
        async with fabrica() as session:
            await SQLAlchemyDeviceRepository(session).obtener_o_crear("PG-CONC-01")
            await session.commit()
        await asyncio.gather(*[_registrar(fabrica, _lectura("PG-CONC-01", i + 1)) for i in range(N_CONCURRENTES)])
        async with fabrica() as session:
            return await VerificarIntegridadRegistroUseCase(SQLAlchemyTrazabilidadRepository(session)).execute()

    resultado = asyncio.run(_con_fabrica(dsn, flujo))
    assert resultado.integra is True


def test_eslabones_concurrentes_de_sistema_no_bifurcan(dsn):
    async def un_evento(fabrica, i):
        async with fabrica() as session:
            await RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session)).execute(
                "PRUEBA_CONCURRENCIA", {"i": i}
            )
            await session.commit()

    async def flujo(fabrica):
        await asyncio.gather(*[un_evento(fabrica, i) for i in range(N_CONCURRENTES * 2)])
        async with fabrica() as session:
            secuencias = (await session.execute(
                select(TraceabilityRecordModel.chain_seq).where(TraceabilityRecordModel.chain_id == "SISTEMA")
            )).scalars().all()
        return sorted(secuencias)

    secuencias = asyncio.run(_con_fabrica(dsn, flujo))
    assert secuencias == list(range(1, len(secuencias) + 1))
