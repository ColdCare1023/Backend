"""HU-29: huella verificable de las tablas de evidencia y alerta crítica si
el respaldo falla. El pg_dump/pg_restore real se ejerce contra PostgreSQL en
el entorno desplegado (docs/operacion/HU-29_respaldo_y_restauracion.md)."""

import logging
from datetime import datetime, timezone

from scripts import respaldo_postgres
from src.infrastructure.database.models import AuditLogModel, DeviceModel, ThermalReadingModel
from src.infrastructure.respaldo.huellas import comparar_huellas, huellas_evidencia


async def _poblar(session_factory, temperatura: float = 5.0) -> None:
    async with session_factory() as session:
        session.add(DeviceModel(id="FARM-HU29-01"))
        await session.flush()
        session.add(ThermalReadingModel(
            device_id="FARM-HU29-01", timestamp=datetime(2026, 10, 1, tzinfo=timezone.utc),
            temperatura_interna=temperatura, origen="dispositivo",
        ))
        session.add(AuditLogModel(accion="PRUEBA", recurso="x", detalle={"b": 1, "a": 2}))
        await session.commit()


async def test_la_huella_es_estable_y_detecta_un_valor_alterado(db_session_factory):
    await _poblar(db_session_factory)
    async with db_session_factory() as session:
        primera = await huellas_evidencia(session)
        segunda = await huellas_evidencia(session)
    assert comparar_huellas(primera, segunda)["coincide"] is True
    assert primera["thermal_readings"]["filas"] == 1

    async with db_session_factory() as session:
        lectura = (await session.execute(ThermalReadingModel.__table__.select())).first()
        await session.execute(
            ThermalReadingModel.__table__.update().where(ThermalReadingModel.id == lectura.id).values(temperatura_interna=9.9)
        )
        await session.commit()
        alterada = await huellas_evidencia(session)

    resultado = comparar_huellas(primera, alterada)
    assert resultado["coincide"] is False
    assert resultado["tablas"]["thermal_readings"] == {
        "filas_origen": 1, "filas_restauradas": 1, "coinciden_filas": True, "coincide_huella": False,
    }
    assert resultado["tablas"]["audit_logs"]["coincide_huella"] is True


def test_una_tabla_faltante_en_la_restauracion_no_coincide():
    origen = {"audit_logs": {"filas": 3, "sha256": "a"}}
    assert comparar_huellas(origen, {})["coincide"] is False


def test_si_el_respaldo_falla_hay_log_critico_y_codigo_de_error(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(respaldo_postgres.shutil, "which", lambda _nombre: None)  # sin pg_dump
    # alembic.fileConfig (test_migraciones) deshabilita los loggers existentes.
    monkeypatch.setattr(respaldo_postgres.logger, "disabled", False)
    with caplog.at_level(logging.CRITICAL, logger="respaldo"):
        codigo = respaldo_postgres.main(["--destino", str(tmp_path)])

    assert codigo == respaldo_postgres.CODIGO_FALLO
    assert any("RESPALDO FALLIDO" in r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL)


def test_la_url_asyncpg_se_traduce_para_pg_dump():
    assert respaldo_postgres.url_libpq("postgresql+asyncpg://u:p@h:5432/db?ssl=require") == (
        "postgresql://u:p@h:5432/db?sslmode=require"
    )
