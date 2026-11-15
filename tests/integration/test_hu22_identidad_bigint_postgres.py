"""HU-22 / HU-11 (migración 0042): `boot_id` y `seq_no` admiten todo el rango
UINT32 que acepta el contrato v1.0, en PostgreSQL real.

Con INTEGER, una lectura con `seq_no` > 2 147 483 647 pasaba la validación del
contrato y fallaba en el INSERT (NumericValueOutOfRange): sin COMMIT no había
acuse y el nodo reintentaba para siempre con su cola FIFO bloqueada. SQLite no
lo reproduce (sus INTEGER ya son de 64 bits), por eso la prueba es en PostgreSQL."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.application.use_cases.registrar_lectura_termica import RegistrarLecturaTermicaUseCase
from src.domain.entities.lectura_termica import LecturaTermica
from src.infrastructure.ai.random_forest_service import get_random_forest_service
from src.infrastructure.database.models import ThermalReadingModel
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.trazabilidad_repository import SQLAlchemyTrazabilidadRepository
from src.infrastructure.mqtt.contrato_v1 import UINT32
from tests.postgres import RAIZ, a_asyncpg, base_migrada

DEV = "ESP32_BIGINT_01"


def _alembic():
    from alembic.config import Config

    config = Config(str(RAIZ / "alembic.ini"))
    config.set_main_option("script_location", str(RAIZ / "alembic"))
    return config


async def _tipos(dsn: str) -> dict[str, str]:
    motor = create_async_engine(a_asyncpg(dsn))
    try:
        async with motor.connect() as conexion:
            filas = await conexion.execute(
                text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name = 'thermal_readings' AND column_name IN ('boot_id', 'seq_no')"
                )
            )
            return dict(filas.all())
    finally:
        await motor.dispose()


async def _registrar_en_el_tope(dsn: str) -> tuple[int, int]:
    motor = create_async_engine(a_asyncpg(dsn))
    try:
        fabrica = async_sessionmaker(motor, expire_on_commit=False)
        async with fabrica() as session:
            await SQLAlchemyDeviceRepository(session).obtener_o_crear(DEV)
            await session.commit()
        lectura = LecturaTermica(
            device_id=DEV, timestamp=datetime.now(tz=timezone.utc) - timedelta(minutes=1),
            temperatura_ambiental=21.0, humedad_ambiental=50.0, temperatura_interna=5.0,
            apertura_refrigerador=False, estado_conectividad="online", boot_id=UINT32, seq_no=UINT32,
            time_quality="synced",
        )
        async with fabrica() as session:
            await RegistrarLecturaTermicaUseCase(
                SQLAlchemyLecturaRepository(session), SQLAlchemyAlertaRepository(session),
                SQLAlchemyTrazabilidadRepository(session),
                ClasificarRiesgoTermicoUseCase(get_random_forest_service()),
                device_repository=SQLAlchemyDeviceRepository(session),
            ).execute(lectura)
            await session.commit()
        async with fabrica() as session:
            fila = (
                await session.execute(select(ThermalReadingModel).where(ThermalReadingModel.device_id == DEV))
            ).scalar_one()
            return fila.boot_id, fila.seq_no
    finally:
        await motor.dispose()


def test_la_identidad_admite_el_rango_uint32_del_contrato():
    with base_migrada() as dsn:
        # Con INTEGER este INSERT fallaba con NumericValueOutOfRange.
        assert asyncio.run(_registrar_en_el_tope(dsn)) == (UINT32, UINT32)
        assert asyncio.run(_tipos(dsn)) == {"boot_id": "bigint", "seq_no": "bigint"}


def test_downgrade_vuelve_a_integer_y_upgrade_a_bigint():
    from alembic import command

    with base_migrada() as dsn:
        command.downgrade(_alembic(), "0041_hu20_normalizacion_preventiva")
        assert asyncio.run(_tipos(dsn)) == {"boot_id": "integer", "seq_no": "integer"}
        command.upgrade(_alembic(), "head")
        assert asyncio.run(_tipos(dsn)) == {"boot_id": "bigint", "seq_no": "bigint"}


def test_downgrade_se_niega_a_truncar_lecturas_fuera_de_integer():
    from alembic import command

    with base_migrada() as dsn:
        asyncio.run(_registrar_en_el_tope(dsn))
        with pytest.raises(RuntimeError, match="truncaría"):
            command.downgrade(_alembic(), "0041_hu20_normalizacion_preventiva")
        assert asyncio.run(_tipos(dsn)) == {"boot_id": "bigint", "seq_no": "bigint"}
