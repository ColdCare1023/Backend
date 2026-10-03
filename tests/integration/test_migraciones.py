"""B8.2: la cadena de migraciones Alembic aplica limpia desde cero.

La suite crea el esquema con `Base.metadata.create_all`, que no ejecuta ni una
sola migración. Sin esta prueba, un modelo podía divergir de su migración y
nadie se enteraba hasta desplegar: los tests pasaban en verde contra un esquema
que en producción no existía."""

import pathlib
import tempfile

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command

RAIZ = pathlib.Path(__file__).resolve().parents[2]

# Tablas que el sistema necesita para operar; si una migración deja de crearlas
# la prueba falla con el nombre exacto de la que falta.
TABLAS_ESPERADAS = {
    "devices",
    "roles",
    "users",
    "thermal_readings",
    "thermal_alerts",
    "corrective_actions",
    "traceability_records",
    "audit_logs",
    "report_exports",
    "firmware_releases",
    "firmware_deployments",
    "forensic_snapshots",
    "system_state",
    "checklist_bpa",
}


def _configuracion_alembic(url: str) -> Config:
    config = Config(str(RAIZ / "alembic.ini"))
    config.set_main_option("script_location", str(RAIZ / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture
def base_temporal(monkeypatch):
    """SQLite en archivo, no en memoria: Alembic abre y cierra su propia
    conexión, y una base en memoria desaparecería entre migraciones.

    `alembic/env.py` reescribe `sqlalchemy.url` con `get_settings().database_url`,
    así que no basta con pasarla en la Config: hay que apuntar DATABASE_URL a
    esta base e invalidar la caché de settings, o las migraciones se aplicarían
    sobre la base en memoria del conftest y las tablas no se verían aquí."""
    from src.infrastructure.config import get_settings

    with tempfile.TemporaryDirectory() as carpeta:
        ruta = pathlib.Path(carpeta) / "migraciones.db"
        # env.py construye un engine async, así que Alembic necesita aiosqlite;
        # el inspector de estas pruebas es síncrono y usa el driver por defecto.
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{ruta}")
        get_settings.cache_clear()
        try:
            yield f"sqlite:///{ruta}"
        finally:
            get_settings.cache_clear()


def test_upgrade_head_aplica_sin_errores(base_temporal):
    command.upgrade(_configuracion_alembic(base_temporal), "head")

    inspector = inspect(create_engine(base_temporal))
    existentes = set(inspector.get_table_names())
    faltantes = TABLAS_ESPERADAS - existentes
    assert not faltantes, f"la migración no creó: {sorted(faltantes)}"


def test_checklist_bpa_tiene_los_diez_items_y_la_unicidad_por_dia(base_temporal):
    command.upgrade(_configuracion_alembic(base_temporal), "head")
    inspector = inspect(create_engine(base_temporal))

    columnas = {c["name"] for c in inspector.get_columns("checklist_bpa")}
    items = {
        "temperatura",
        "termometro",
        "registros",
        "alertas_revisadas",
        "acciones_documentadas",
        "puerta",
        "limpieza",
        "exclusivo",
        "rotulado",
        "respaldo",
    }
    assert items <= columnas

    # Un checklist por usuario y día: sin esta restricción, el mismo día podría
    # acumular declaraciones contradictorias sin que nada lo impidiera.
    restricciones = inspector.get_unique_constraints("checklist_bpa")
    assert any(
        set(r["column_names"]) == {"usuario_id", "fecha"} for r in restricciones
    ), "falta UNIQUE(usuario_id, fecha)"


def test_devices_tiene_las_columnas_de_calibracion(base_temporal):
    command.upgrade(_configuracion_alembic(base_temporal), "head")
    inspector = inspect(create_engine(base_temporal))

    columnas = {c["name"] for c in inspector.get_columns("devices")}
    assert {
        "fecha_ultima_calibracion",
        "numero_certificado_calibracion",
        "fecha_proxima_calibracion",
        "observaciones_calibracion",
    } <= columnas


def test_indices_de_consulta_existen(base_temporal):
    """B-11: sin estos índices el historial degrada a scan secuencial conforme
    crece la serie temporal."""
    command.upgrade(_configuracion_alembic(base_temporal), "head")
    inspector = inspect(create_engine(base_temporal))

    for tabla, indice in (
        ("thermal_readings", "idx_thermal_readings_device_ts"),
        ("thermal_alerts", "idx_thermal_alerts_device_created"),
        ("traceability_records", "idx_traceability_records_created"),
        ("audit_logs", "idx_audit_logs_created"),
    ):
        nombres = {i["name"] for i in inspector.get_indexes(tabla)}
        assert indice in nombres, f"falta el índice {indice} en {tabla}"


def _esquema_completo(engine) -> dict[str, set[tuple[str, bool]]]:
    """Todas las tablas y sus columnas (nombre, nullable), para comparar el
    esquema entero sin atarse a los nombres de columna de la migración que
    sea la última hoy. Incluye `nullable` porque no toda migración agrega o
    quita columnas — algunas solo relajan/endurecen una restricción (p. ej.
    0012_hu04_mc38_ausente), y esa diferencia también debe sobrevivir un
    downgrade+upgrade."""
    inspector = inspect(engine)
    return {
        tabla: {(c["name"], c["nullable"]) for c in inspector.get_columns(tabla)}
        for tabla in inspector.get_table_names()
    }


def test_downgrade_deshace_la_ultima_migracion(base_temporal):
    """Una migración sin downgrade correcto deja el despliegue sin salida si
    hay que revertir: downgrade(-1) seguido de upgrade(head) debe devolver
    EXACTAMENTE el mismo esquema que un upgrade(head) directo — sin importar
    cuál sea la última migración en cada momento (no se ata a sus nombres de
    columna, así no hay que reescribir esta prueba cada vez que se agrega
    una migración nueva)."""
    config = _configuracion_alembic(base_temporal)
    command.upgrade(config, "head")
    esquema_tras_upgrade_directo = _esquema_completo(create_engine(base_temporal))

    command.downgrade(config, "-1")
    esquema_tras_downgrade = _esquema_completo(create_engine(base_temporal))
    assert esquema_tras_downgrade != esquema_tras_upgrade_directo, (
        "el downgrade no cambió el esquema en absoluto — probablemente no está implementado"
    )

    command.upgrade(config, "head")
    esquema_tras_reupgrade = _esquema_completo(create_engine(base_temporal))
    assert esquema_tras_reupgrade == esquema_tras_upgrade_directo, (
        "downgrade(-1) + upgrade(head) no reproduce el esquema original"
    )
