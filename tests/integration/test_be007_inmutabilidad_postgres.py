"""BE-007: PostgreSQL rechaza modificar o borrar la evidencia (migración 0018).

Requiere `pgserver` (PostgreSQL embebido, requirements-dev). Sin él la prueba
se omite: SQLite, que usa el resto de la suite, no tiene triggers PL/pgSQL."""

import asyncio
import pathlib
import tempfile
import uuid

import pytest
from alembic.config import Config

from alembic import command

pgserver = pytest.importorskip("pgserver")
asyncpg = pytest.importorskip("asyncpg")

RAIZ = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def postgres():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as carpeta:
        servidor = pgserver.get_server(carpeta, cleanup_mode="stop")
        servidor.psql("CREATE DATABASE evidencia;")
        socket = servidor.get_uri().split("host=")[1]
        try:
            yield socket
        finally:
            servidor.cleanup()


@pytest.fixture(scope="module")
def migrada(postgres):
    from src.infrastructure.config import get_settings

    url = f"postgresql+asyncpg://postgres@/evidencia?host={postgres}"
    parche = pytest.MonkeyPatch()
    parche.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        config = Config(str(RAIZ / "alembic.ini"))
        config.set_main_option("script_location", str(RAIZ / "alembic"))
        command.upgrade(config, "head")
        yield postgres
    finally:
        parche.undo()
        get_settings.cache_clear()


def _ejecutar(socket: str, *sentencias: str):
    async def _correr():
        conexion = await asyncpg.connect(user="postgres", database="evidencia", host=socket)
        try:
            resultado = None
            for sentencia in sentencias:
                resultado = await conexion.execute(sentencia)
            return resultado
        finally:
            await conexion.close()

    return asyncio.run(_correr())


def _rechaza(socket: str, sentencia: str) -> bool:
    try:
        _ejecutar(socket, sentencia)
    except asyncpg.exceptions.InsufficientPrivilegeError:
        return True
    return False


def test_audit_logs_no_admite_update_ni_delete(migrada):
    fila = uuid.uuid4()
    _ejecutar(
        migrada,
        f"INSERT INTO audit_logs (id, accion, recurso, detalle) VALUES ('{fila}', 'X', 'r', '{{}}')",
    )
    assert _rechaza(migrada, f"UPDATE audit_logs SET accion = 'Y' WHERE id = '{fila}'")
    assert _rechaza(migrada, f"DELETE FROM audit_logs WHERE id = '{fila}'")


def test_trazabilidad_solo_admite_marcas_de_aislamiento(migrada):
    fila = uuid.uuid4()
    _ejecutar(
        migrada,
        "INSERT INTO traceability_records (id, tipo_evento, payload, timestamp, previous_hash, hash_actual, "
        "chain_id, chain_seq, hash_version) VALUES "
        f"('{fila}', 'E', '{{}}', now(), '{'0' * 64}', '{uuid.uuid4().hex * 2}', 'C', 1, 3)",
    )
    _ejecutar(migrada, f"UPDATE traceability_records SET is_corrupted = true WHERE id = '{fila}'")
    assert _rechaza(migrada, f"UPDATE traceability_records SET payload = '{{\"x\": 1}}' WHERE id = '{fila}'")
    assert _rechaza(migrada, f"DELETE FROM traceability_records WHERE id = '{fila}'")


def test_lecturas_solo_admiten_reanclaje(migrada):
    fila = uuid.uuid4()
    _ejecutar(
        migrada,
        "INSERT INTO devices (id, estado_conectividad, activo, firmware_version) "
        "VALUES ('D-PG', 'online', true, '1.0.0') ON CONFLICT DO NOTHING",
        "INSERT INTO thermal_readings (id, device_id, timestamp, temperatura_interna, excursion_confirmada, origen) "
        f"VALUES ('{fila}', 'D-PG', now(), 5.0, false, 'dispositivo')",
    )
    _ejecutar(migrada, f"UPDATE thermal_readings SET timestamp_estimado = now() WHERE id = '{fila}'")
    assert _rechaza(migrada, f"UPDATE thermal_readings SET temperatura_interna = 4.0 WHERE id = '{fila}'")
    assert _rechaza(migrada, f"DELETE FROM thermal_readings WHERE id = '{fila}'")


def test_la_aplicacion_sigue_operando_con_los_triggers(migrada):
    """Ingesta con reanclaje (UPDATE de timestamp_estimado), cadena con bloqueo
    advisory de PostgreSQL y aislamiento (UPDATE de marcas): todo lo que la
    aplicación modifica legítimamente sigue permitido."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
    from src.application.use_cases.gestionar_corrupcion_cadena import AislarCorrupcionUseCase
    from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
    from src.application.use_cases.registrar_lectura_termica import RegistrarLecturaTermicaUseCase
    from src.domain.entities.lectura_termica import LecturaTermica
    from src.infrastructure.ai.random_forest_service import get_random_forest_service
    from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
    from src.infrastructure.database.repositories.corrupcion_repository import SQLAlchemyCorrupcionRepository
    from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
    from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
    from src.infrastructure.database.repositories.trazabilidad_repository import (
        SQLAlchemyTrazabilidadRepository,
    )

    async def _flujo():
        motor = create_async_engine(f"postgresql+asyncpg://postgres@/evidencia?host={migrada}")
        fabrica = async_sessionmaker(motor, expire_on_commit=False)
        ahora = datetime.now(tz=timezone.utc)
        try:
            async with fabrica() as session:
                await SQLAlchemyDeviceRepository(session).obtener_o_crear("D-APP")
                lecturas = SQLAlchemyLecturaRepository(session)
                caso = RegistrarLecturaTermicaUseCase(
                    lecturas, SQLAlchemyAlertaRepository(session), SQLAlchemyTrazabilidadRepository(session),
                    ClasificarRiesgoTermicoUseCase(get_random_forest_service()),
                    device_repository=SQLAlchemyDeviceRepository(session),
                )
                base = {
                    "device_id": "D-APP", "temperatura_ambiental": 20.0, "humedad_ambiental": 50.0,
                    "temperatura_interna": 5.0, "apertura_refrigerador": False,
                    "estado_conectividad": "online", "boot_id": 7,
                }
                await caso.execute(LecturaTermica(**base, timestamp=ahora - timedelta(hours=3), seq_no=1,
                                                  time_quality="unsynced", uptime_ms=1_000))
                await caso.execute(LecturaTermica(**base, timestamp=ahora, seq_no=2,
                                                  time_quality="synced", uptime_ms=61_000))
                assert await lecturas.reanclar_no_sincronizadas("D-APP", 7) == 1
                await session.commit()

            async with fabrica() as session:
                repo = SQLAlchemyTrazabilidadRepository(session)
                registro = await RegistrarHashEncadenadoUseCase(repo).execute("E", {"x": 1}, device_id="D-APP")
                await session.commit()
                # Manipulación de quien SÍ puede desactivar triggers (superusuario):
                # el aislamiento debe seguir pudiendo marcar el resultado.
                tabla = "ALTER TABLE traceability_records {} TRIGGER traceability_records_solo_marcas"
                await session.execute(text(tabla.format("DISABLE")))
                await session.execute(
                    text(f"UPDATE traceability_records SET payload = '{{\"x\": 2}}' WHERE id = '{registro.id}'")
                )
                await session.execute(text(tabla.format("ENABLE")))
                await session.commit()

            async with fabrica() as session:
                repo = SQLAlchemyTrazabilidadRepository(session)
                await AislarCorrupcionUseCase(
                    repo, SQLAlchemyCorrupcionRepository(session), RegistrarHashEncadenadoUseCase(repo)
                ).execute(registro.id)
                await session.commit()
                assert await repo.esta_marcado_corrupto(registro.id)
        finally:
            await motor.dispose()

    asyncio.run(_flujo())
