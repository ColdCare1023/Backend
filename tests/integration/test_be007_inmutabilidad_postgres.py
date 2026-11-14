"""BE-007: PostgreSQL rechaza modificar o borrar la evidencia (migración 0018).

Usa PostgreSQL real (tests/postgres.py): el servicio del CI
(`TEST_POSTGRES_URL`) o `pgserver` en local. SQLite, que usa el resto de la
suite, no tiene triggers PL/pgSQL."""

import asyncio
import uuid

import pytest

from tests.postgres import a_asyncpg, base_migrada

asyncpg = pytest.importorskip("asyncpg")


@pytest.fixture(scope="module")
def migrada():
    with base_migrada() as dsn:
        yield dsn


def _ejecutar(dsn: str, *sentencias: str):
    async def _correr():
        conexion = await asyncpg.connect(dsn)
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
        motor = create_async_engine(a_asyncpg(migrada))
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


def _rechaza_con(dsn: str, sentencia: str, *errores) -> bool:
    try:
        _ejecutar(dsn, sentencia)
    except errores:
        return True
    return False


def _accion_correctiva(dsn: str) -> uuid.UUID:
    """Usuario, dispositivo, lectura, alerta y acción correctiva reales (ORM)."""
    from datetime import datetime, timezone

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from src.domain.entities.usuario import Usuario
    from src.domain.value_objects.rol import Rol
    from src.infrastructure.database.models import (
        CorrectiveActionModel,
        DeviceModel,
        ThermalAlertModel,
        ThermalReadingModel,
    )
    from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository

    async def _crear() -> uuid.UUID:
        motor = create_async_engine(a_asyncpg(dsn))
        try:
            async with async_sessionmaker(motor, expire_on_commit=False)() as session:
                usuario = await SQLAlchemyUsuarioRepository(session).agregar(
                    Usuario(nombre="QF", email=f"qf-{uuid.uuid4().hex[:8]}@farmacia.example.org",
                            password_hash="x", rol=Rol.FARMACEUTICO)
                )
                device = f"D-HU28-{uuid.uuid4().hex[:6]}"
                session.add(DeviceModel(id=device))
                await session.flush()
                lectura = ThermalReadingModel(
                    device_id=device, timestamp=datetime.now(tz=timezone.utc), temperatura_interna=11.0
                )
                session.add(lectura)
                await session.flush()
                alerta = ThermalAlertModel(
                    reading_id=lectura.id, device_id=device, nivel_riesgo="excursion_critica", mensaje="x",
                    lectura_inicial_id=lectura.id, lectura_mas_reciente_id=lectura.id,
                )
                session.add(alerta)
                await session.flush()
                accion = CorrectiveActionModel(
                    alert_id=alerta.id, usuario_id=usuario.id, descripcion="Traslado", decision="Trasladar",
                    justificacion="Fuera de rango",
                )
                session.add(accion)
                await session.commit()
                return accion.id
        finally:
            await motor.dispose()

    return asyncio.run(_crear())


def test_acciones_correctivas_no_admiten_update_ni_delete(migrada):
    """HU-28 (migración 0028): la rectificación conserva el original porque la
    base rechaza reescribirlo o borrarlo, no solo porque la API no lo ofrezca."""
    fila = _accion_correctiva(migrada)
    restringido = asyncpg.exceptions.RestrictViolationError
    assert _rechaza_con(migrada, f"UPDATE corrective_actions SET justificacion = 'otra' WHERE id = '{fila}'", restringido)
    assert _rechaza_con(migrada, f"DELETE FROM corrective_actions WHERE id = '{fila}'", restringido)


def test_historial_de_configuracion_es_de_solo_anexado(migrada):
    """HU-49 (migración 0039): valor anterior/nuevo, actor y fecha no se reescriben."""
    fila = uuid.uuid4()
    _ejecutar(
        migrada,
        "INSERT INTO devices (id, estado_conectividad, activo, firmware_version) "
        "VALUES ('D-H49', 'online', true, '1.0.0') ON CONFLICT DO NOTHING",
        "INSERT INTO device_config_history (id, device_id, campo, valor_anterior, valor_nuevo) "
        f"VALUES ('{fila}', 'D-H49', 'ubicacion', 'A', 'B')",
    )
    restringido = asyncpg.exceptions.RestrictViolationError
    assert _rechaza_con(migrada, f"UPDATE device_config_history SET valor_nuevo = 'X' WHERE id = '{fila}'", restringido)
    assert _rechaza_con(migrada, f"DELETE FROM device_config_history WHERE id = '{fila}'", restringido)


def test_audit_logs_resultado_admite_solo_los_cuatro_valores(migrada):
    """HU-42 (migración 0039): resultado NULL (filas previas) o uno de los cuatro."""
    _ejecutar(
        migrada,
        "INSERT INTO audit_logs (id, accion, recurso, detalle, resultado) "
        f"VALUES ('{uuid.uuid4()}', 'RBAC_DENEGADO', 'GET /api/usuarios', '{{}}', 'denegado')",
        f"INSERT INTO audit_logs (id, accion, recurso, detalle) VALUES ('{uuid.uuid4()}', 'X', 'r', '{{}}')",
    )
    assert _rechaza_con(
        migrada,
        "INSERT INTO audit_logs (id, accion, recurso, detalle, resultado) "
        f"VALUES ('{uuid.uuid4()}', 'X', 'r', '{{}}', 'quizas')",
        asyncpg.exceptions.CheckViolationError,
    )


def test_la_migracion_0039_se_revierte_en_postgres():
    """Desplegable con vuelta atrás: downgrade quita columna, restricción,
    índice y trigger; upgrade los repone."""
    from alembic.config import Config

    from alembic import command
    from tests.postgres import RAIZ

    async def _estado(dsn: str) -> tuple[bool, bool]:
        conexion = await asyncpg.connect(dsn)
        try:
            columna = await conexion.fetchval(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'audit_logs' AND column_name = 'resultado'"
            )
            trigger = await conexion.fetchval(
                "SELECT count(*) FROM pg_trigger WHERE tgname = 'device_config_history_solo_anexado'"
            )
            return bool(columna), bool(trigger)
        finally:
            await conexion.close()

    with base_migrada() as dsn:
        config = Config(str(RAIZ / "alembic.ini"))
        config.set_main_option("script_location", str(RAIZ / "alembic"))
        assert asyncio.run(_estado(dsn)) == (True, True)
        command.downgrade(config, "0038_codigos_verificacion")
        assert asyncio.run(_estado(dsn)) == (False, False)
        command.upgrade(config, "head")
        assert asyncio.run(_estado(dsn)) == (True, True)
