"""HU-29: huella verificable de las tablas de evidencia y alerta crítica si
el respaldo falla. El pg_dump/pg_restore real se ejerce contra PostgreSQL en
el entorno desplegado (docs/operacion/HU-29_respaldo_y_restauracion.md)."""

import logging
import uuid
from datetime import datetime, timezone

import pytest

from scripts import respaldo_postgres
from src.infrastructure.database.models import AuditLogModel, DeviceModel, ThermalReadingModel
from src.infrastructure.respaldo import huellas as huellas_modulo
from src.infrastructure.respaldo.huellas import (
    TABLAS_EVIDENCIA,
    TABLAS_EVIDENCIA_LEGADO,
    columnas_de_manifiesto,
    comparar_huellas,
    huellas_evidencia,
)


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


async def test_la_huella_de_la_bitacora_incluye_el_resultado(db_session_factory):
    """HU-42/HU-29 (P2 de la re-auditoría): `audit_logs.resultado` es evidencia;
    una restauración que lo altere no puede dar una huella idéntica."""
    await _poblar(db_session_factory)
    async with db_session_factory() as session:
        antes = await huellas_evidencia(session)
        await session.execute(AuditLogModel.__table__.update().values(resultado="denegado"))
        await session.commit()
        despues = await huellas_evidencia(session)

    assert "resultado" in TABLAS_EVIDENCIA["audit_logs"]
    resultado = comparar_huellas(antes, despues)
    assert resultado["coincide"] is False
    assert resultado["tablas"]["audit_logs"]["coincide_huella"] is False
    assert resultado["tablas"]["thermal_readings"]["coincide_huella"] is True


async def test_un_manifiesto_anterior_se_verifica_con_sus_columnas(db_session_factory):
    """Un manifiesto generado antes de declarar `columnas` se calculó sin
    `resultado`: verificarlo con las columnas actuales fallaría aunque la
    restauración fuera exacta."""
    await _poblar(db_session_factory)
    async with db_session_factory() as session:
        legado = await huellas_evidencia(session, TABLAS_EVIDENCIA_LEGADO)
        actual = await huellas_evidencia(session)
        manifiesto_antiguo = {"huellas": legado}
        manifiesto_nuevo = {
            "huellas": actual, "columnas": {t: list(c) for t, c in TABLAS_EVIDENCIA.items()},
        }
        restaurada_antigua = await huellas_evidencia(session, columnas_de_manifiesto(manifiesto_antiguo))
        restaurada_nueva = await huellas_evidencia(session, columnas_de_manifiesto(manifiesto_nuevo))

    assert legado["audit_logs"]["sha256"] != actual["audit_logs"]["sha256"]
    assert comparar_huellas(manifiesto_antiguo["huellas"], restaurada_antigua)["coincide"] is True
    assert comparar_huellas(manifiesto_nuevo["huellas"], restaurada_nueva)["coincide"] is True


async def test_columnas_desconocidas_en_un_manifiesto_se_rechazan(db_session_factory):
    async with db_session_factory() as session:
        with pytest.raises(ValueError):
            await huellas_evidencia(session, {"audit_logs": ("id", "1; DROP TABLE audit_logs")})
        with pytest.raises(ValueError):
            await huellas_evidencia(session, {"otra_tabla": ("id",)})


# ── P0-4 (b): tablas de evidencia ampliadas y paginación por clave ──────────


def test_la_huella_cubre_usuarios_equipos_alertas_acciones_y_configuracion():
    """HU-29 esc. 2: «los usuarios y la metadata crítica se recuperan al 100 %»."""
    for tabla in ("users", "devices", "thermal_alerts", "corrective_actions", "device_config_history"):
        assert tabla in TABLAS_EVIDENCIA
        assert "id" in TABLAS_EVIDENCIA[tabla]
    # Un manifiesto anterior (sin `columnas`) se verifica solo con sus tres tablas.
    assert set(TABLAS_EVIDENCIA_LEGADO) == {"thermal_readings", "traceability_records", "audit_logs"}


async def test_un_usuario_perdido_en_la_restauracion_se_detecta(db_session_factory, crear_usuario):
    from src.domain.value_objects.rol import Rol
    from src.infrastructure.database.models import UserModel

    await _poblar(db_session_factory)
    await crear_usuario("QF Respaldo", "qf.respaldo@coldcare.test", "Clave-Segura-123", Rol.FARMACEUTICO)
    async with db_session_factory() as session:
        antes = await huellas_evidencia(session)
        await session.execute(UserModel.__table__.delete())
        despues = await huellas_evidencia(session)

    resultado = comparar_huellas(antes, despues)
    assert resultado["coincide"] is False
    assert resultado["tablas"]["users"]["coinciden_filas"] is False


class _InsertaTrasLaPrimeraPagina:
    """Simula al nodo publicando mientras se recorre la tabla: tras leer la
    primera página de lecturas, entra una fila cuyo id ordena ANTES que todas."""

    def __init__(self, session) -> None:
        self._session = session
        self._paginas = 0

    async def execute(self, sentencia, *args, **kwargs):
        resultado = await self._session.execute(sentencia, *args, **kwargs)
        consulta = str(sentencia)
        if "FROM thermal_readings" in consulta and "LIMIT" in consulta:
            self._paginas += 1
            if self._paginas == 1:
                await self._session.execute(ThermalReadingModel.__table__.insert().values(
                    id=uuid.UUID(int=0), device_id="FARM-HU29-01", timestamp=datetime(2026, 10, 2, tzinfo=timezone.utc),
                    temperatura_interna=4.0, origen="dispositivo",
                ))
        return resultado


async def test_la_paginacion_por_clave_no_duplica_ni_salta_filas(db_session_factory, monkeypatch):
    """Con OFFSET, una fila nueva con id menor desplaza las páginas: la última
    fila leída vuelve a entrar y otra se salta. Por clave (`id > última`), la
    huella es la del estado en que empezó el recorrido."""
    async with db_session_factory() as session:
        session.add(DeviceModel(id="FARM-HU29-01"))
        await session.flush()
        for minuto in range(3):
            session.add(ThermalReadingModel(
                device_id="FARM-HU29-01", timestamp=datetime(2026, 10, 1, 0, minuto, tzinfo=timezone.utc),
                temperatura_interna=5.0 + minuto, origen="dispositivo",
            ))
        await session.commit()
    async with db_session_factory() as session:
        esperada = await huellas_evidencia(session, {"thermal_readings": TABLAS_EVIDENCIA["thermal_readings"]})
    monkeypatch.setattr(huellas_modulo, "LOTE", 1)

    async with db_session_factory() as session:
        recorrida = await huellas_evidencia(
            _InsertaTrasLaPrimeraPagina(session), {"thermal_readings": TABLAS_EVIDENCIA["thermal_readings"]}
        )

    assert recorrida == esperada
