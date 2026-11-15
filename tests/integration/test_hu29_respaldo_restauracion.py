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


# ── HU-29 esc. 2: contenido de la evidencia y metadata del modelo IA ────────


async def test_la_huella_detecta_un_eslabon_con_otro_contenido(db_session_factory):
    """Una restauración que cambie el payload, la hora o el chain_seq de un
    eslabón (aunque conserve sus hashes) no puede dar la misma huella."""
    from sqlalchemy import text

    from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
    from src.infrastructure.database.repositories.trazabilidad_repository import (
        SQLAlchemyTrazabilidadRepository,
    )

    async with db_session_factory() as session:
        await RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session)).execute(
            tipo_evento="LECTURA_TERMICA", payload={"temperatura_interna": 5.0}, device_id="DEV-HU29"
        )
        await session.commit()
    async with db_session_factory() as session:
        antes = await huellas_evidencia(session)
    for cambio in (
        "payload='{\"temperatura_interna\": 9.9}'",
        "timestamp='2000-01-01 00:00:00'",
        "chain_seq=99",
    ):
        async with db_session_factory() as session:
            await session.execute(text(f"UPDATE traceability_records SET {cambio}"))
            despues = await huellas_evidencia(session)
            await session.rollback()
        resultado = comparar_huellas(antes, despues)
        assert resultado["tablas"]["traceability_records"]["coincide_huella"] is False, cambio
        assert resultado["tablas"]["thermal_readings"]["coincide_huella"] is True


async def test_la_huella_cubre_el_registro_de_versiones_del_modelo(db_session_factory, crear_usuario):
    """HU-46/HU-47: el registro de versiones del modelo IA es metadata crítica."""
    from src.domain.value_objects.rol import Rol
    from src.infrastructure.database.models import ModeloIAVersionModel

    admin = await crear_usuario("Admin HU29", "admin.hu29@farmacia.example.org", "password123", Rol.ADMINISTRADOR)
    async with db_session_factory() as session:
        session.add(ModeloIAVersionModel(
            version="1.0.0", model_hash="a" * 64, feature_schema_version="v3", dataset_version="d1",
            scikit_learn_version="1.9.0", python_version="3.12", trained_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            aprobado_por=admin.id, activa=True,
        ))
        await session.commit()
        antes = await huellas_evidencia(session)
        await session.execute(ModeloIAVersionModel.__table__.update().values(activa=False))
        despues = await huellas_evidencia(session)

    assert antes["ai_model_versions"]["filas"] == 1
    assert comparar_huellas(antes, despues)["tablas"]["ai_model_versions"]["coincide_huella"] is False


async def test_un_manifiesto_v2_se_sigue_verificando_con_sus_columnas(db_session_factory):
    """Los manifiestos de HU-29 v2 declaran las columnas de entonces (sin el
    contenido de los eslabones ni ai_model_versions): se verifican con ellas."""
    await _poblar(db_session_factory)
    columnas_v2 = {
        tabla: list(cols) for tabla, cols in TABLAS_EVIDENCIA.items() if tabla != "ai_model_versions"
    }
    columnas_v2["traceability_records"] = list(TABLAS_EVIDENCIA_LEGADO["traceability_records"])
    columnas_v2["thermal_readings"] = list(TABLAS_EVIDENCIA_LEGADO["thermal_readings"])
    async with db_session_factory() as session:
        origen = await huellas_evidencia(session, {t: tuple(c) for t, c in columnas_v2.items()})
        manifiesto = {"huellas": origen, "columnas": columnas_v2}
        restaurada = await huellas_evidencia(session, columnas_de_manifiesto(manifiesto))

    assert set(restaurada) == set(columnas_v2)
    assert comparar_huellas(manifiesto["huellas"], restaurada)["coincide"] is True


# ── P0-4 (b): tablas de evidencia ampliadas y paginación por clave ──────────


def test_la_huella_cubre_usuarios_equipos_alertas_acciones_y_configuracion():
    """HU-29 esc. 2: «los usuarios y la metadata crítica se recuperan al 100 %»."""
    for tabla in (
        "users", "devices", "thermal_alerts", "corrective_actions", "device_config_history", "ai_model_versions",
    ):
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


# ── HU-29 esc. 3: el backend detecta el fallo de la tarea programada ────────


async def _bitacora_respaldo(session_factory, accion: str, cuando: datetime) -> None:
    async with session_factory() as session:
        session.add(AuditLogModel(accion=accion, recurso="respaldos", detalle={}, created_at=cuando))
        await session.commit()


@pytest.fixture
def log_tareas(monkeypatch, caplog):
    from src.interface import tareas

    # alembic.fileConfig (test_migraciones) deshabilita los loggers existentes.
    monkeypatch.setattr(tareas.logger, "disabled", False)
    caplog.set_level(logging.WARNING, logger=tareas.logger.name)
    return caplog


def _criticos(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL]


async def test_un_respaldo_fallido_deja_alerta_critica_en_el_log_del_backend(db_session_factory, log_tareas):
    from src.interface.tareas import VigilanteRespaldos

    ahora = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    vigilante = VigilanteRespaldos(db_session_factory, 3600, max_antiguedad_horas=0)
    assert await vigilante.ejecutar_una_vez(ahora) is None  # sin respaldos: nada que vigilar
    assert _criticos(log_tareas) == []

    await _bitacora_respaldo(db_session_factory, "RESPALDO_COMPLETADO", ahora.replace(hour=2))
    await _bitacora_respaldo(db_session_factory, "RESPALDO_FALLIDO", ahora.replace(hour=7))
    assert await vigilante.ejecutar_una_vez(ahora) == "fallido"
    assert await vigilante.ejecutar_una_vez(ahora) == "fallido"
    criticos = _criticos(log_tareas)
    assert len(criticos) == 1 and "respaldo" in criticos[0].lower()  # una vez por fallo

    await _bitacora_respaldo(db_session_factory, "RESPALDO_COMPLETADO", ahora.replace(hour=8))
    assert await vigilante.ejecutar_una_vez(ahora) is None
    assert vigilante.ultimo_completado == ahora.replace(hour=8)


async def test_sin_respaldo_reciente_avisa_solo_si_se_configura_el_plazo(db_session_factory, log_tareas):
    from datetime import timedelta

    from src.interface.tareas import VigilanteRespaldos

    ahora = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    await _bitacora_respaldo(db_session_factory, "RESPALDO_COMPLETADO", ahora - timedelta(hours=30))

    assert await VigilanteRespaldos(db_session_factory, 3600, 0).ejecutar_una_vez(ahora) is None
    assert _criticos(log_tareas) == []
    assert await VigilanteRespaldos(db_session_factory, 3600, 26).ejecutar_una_vez(ahora) == "vencido"
    assert len(_criticos(log_tareas)) == 1
    assert await VigilanteRespaldos(db_session_factory, 3600, 48).ejecutar_una_vez(ahora) is None


def test_health_detalle_informa_la_vigilancia_de_respaldos(client, token_admin):
    from tests.conftest import auth_header

    cuerpo = client.get("/health/detalle", headers=auth_header(token_admin)).json()

    assert set(cuerpo["respaldos"]) == {"vigilancia_activa", "estado", "ultimo_completado", "ultima_pasada"}
