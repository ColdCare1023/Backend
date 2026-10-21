"""BE-007: tablas de evidencia de solo anexado, aplicado por PostgreSQL.

La inmutabilidad de `audit_logs`, `traceability_records` y `thermal_readings`
era una convención de la aplicación: el rol de base de datos podía modificar o
borrar cualquier fila. Estos triggers la convierten en una restricción:

- `audit_logs`: sin UPDATE ni DELETE.
- `traceability_records`: sin DELETE; UPDATE solo de las marcas de
  aislamiento (`is_corrupted`, `is_after_corruption`, HU-47).
- `thermal_readings`: sin DELETE; UPDATE solo de `timestamp_estimado`
  (reanclaje de lecturas sin NTP, migración 0016).

En SQLite (desarrollo) se crean triggers equivalentes con `RAISE(ABORT)`.
Un superusuario de PostgreSQL puede deshabilitarlos; la protección frente a
él es la cadena HMAC (v3).

Revision ID: 0018_evidencia_solo_anexado
Revises: 0017_origen_lecturas
Create Date: 2026-10-21

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0018_evidencia_solo_anexado"
down_revision: str | None = "0017_origen_lecturas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Una sentencia por llamada: asyncpg (driver de producción) no admite varios
# comandos en un mismo `execute`.
_SENTENCIAS_UPGRADE = [
    """
    CREATE OR REPLACE FUNCTION evidencia_rechazar_cambio() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'tabla de evidencia de solo anexado: % no permitido en %', TG_OP, TG_TABLE_NAME
            USING ERRCODE = 'insufficient_privilege';
    END
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE OR REPLACE FUNCTION evidencia_permitir_columnas() RETURNS trigger AS $$
    DECLARE
        mutables text[] := TG_ARGV;
    BEGIN
        IF (to_jsonb(NEW) - mutables) IS DISTINCT FROM (to_jsonb(OLD) - mutables) THEN
            RAISE EXCEPTION 'tabla de evidencia de solo anexado: solo se pueden modificar % en %',
                array_to_string(mutables, ', '), TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER audit_logs_solo_anexado
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION evidencia_rechazar_cambio()
    """,
    """
    CREATE TRIGGER traceability_records_sin_borrado
        BEFORE DELETE ON traceability_records
        FOR EACH ROW EXECUTE FUNCTION evidencia_rechazar_cambio()
    """,
    """
    CREATE TRIGGER traceability_records_solo_marcas
        BEFORE UPDATE ON traceability_records
        FOR EACH ROW EXECUTE FUNCTION evidencia_permitir_columnas('is_corrupted', 'is_after_corruption')
    """,
    """
    CREATE TRIGGER thermal_readings_sin_borrado
        BEFORE DELETE ON thermal_readings
        FOR EACH ROW EXECUTE FUNCTION evidencia_rechazar_cambio()
    """,
    """
    CREATE TRIGGER thermal_readings_solo_reanclaje
        BEFORE UPDATE ON thermal_readings
        FOR EACH ROW EXECUTE FUNCTION evidencia_permitir_columnas('timestamp_estimado')
    """,
]

_SENTENCIAS_DOWNGRADE = [
    "DROP TRIGGER IF EXISTS thermal_readings_solo_reanclaje ON thermal_readings",
    "DROP TRIGGER IF EXISTS thermal_readings_sin_borrado ON thermal_readings",
    "DROP TRIGGER IF EXISTS traceability_records_solo_marcas ON traceability_records",
    "DROP TRIGGER IF EXISTS traceability_records_sin_borrado ON traceability_records",
    "DROP TRIGGER IF EXISTS audit_logs_solo_anexado ON audit_logs",
    "DROP FUNCTION IF EXISTS evidencia_permitir_columnas()",
    "DROP FUNCTION IF EXISTS evidencia_rechazar_cambio()",
]


# SQLite: `BEFORE UPDATE OF <columnas>` protege todo salvo las columnas que la
# aplicación modifica legítimamente.
_COLUMNAS_FIJAS_TRAZABILIDAD = (
    "id, tipo_evento, device_id, usuario_id, payload, timestamp, previous_hash, hash_actual, "
    "created_at, chain_id, chain_seq, hash_version"
)
_COLUMNAS_FIJAS_LECTURAS = (
    "id, device_id, timestamp, temperatura_ambiental, humedad_ambiental, temperatura_interna, "
    "apertura_refrigerador, nivel_riesgo, estado_conectividad, payload, created_at, modelo_version, "
    "confianza_ia, origen_clasificacion, estado_inferencia, motivo_no_inferencia, reading_id, "
    "schema_version, excursion_confirmada, riesgo_efectivo, probabilidades_ia, vector_features_ia, "
    "boot_id, seq_no, time_quality, received_at, uptime_ms, origen, registrado_por"
)
_RECHAZO_SQLITE = "SELECT RAISE(ABORT, 'tabla de evidencia de solo anexado')"
_TRIGGERS_SQLITE = {
    "audit_logs_solo_anexado_upd": "BEFORE UPDATE ON audit_logs",
    "audit_logs_solo_anexado_del": "BEFORE DELETE ON audit_logs",
    "traceability_records_sin_borrado": "BEFORE DELETE ON traceability_records",
    "traceability_records_solo_marcas": f"BEFORE UPDATE OF {_COLUMNAS_FIJAS_TRAZABILIDAD} ON traceability_records",
    "thermal_readings_sin_borrado": "BEFORE DELETE ON thermal_readings",
    "thermal_readings_solo_reanclaje": f"BEFORE UPDATE OF {_COLUMNAS_FIJAS_LECTURAS} ON thermal_readings",
}


def _dialecto() -> str:
    return op.get_bind().dialect.name


def upgrade() -> None:
    if _dialecto() == "postgresql":
        for sentencia in _SENTENCIAS_UPGRADE:
            op.execute(sentencia)
    elif _dialecto() == "sqlite":
        for nombre, evento in _TRIGGERS_SQLITE.items():
            op.execute(f"CREATE TRIGGER {nombre} {evento} BEGIN {_RECHAZO_SQLITE}; END")


def downgrade() -> None:
    if _dialecto() == "postgresql":
        for sentencia in _SENTENCIAS_DOWNGRADE:
            op.execute(sentencia)
    elif _dialecto() == "sqlite":
        for nombre in _TRIGGERS_SQLITE:
            op.execute(f"DROP TRIGGER IF EXISTS {nombre}")
