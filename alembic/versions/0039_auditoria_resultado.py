"""HU-42/HU-50: resultado de cada evento de la bitácora; HU-49: historial de
configuración de solo anexado.

- audit_logs.resultado (exitoso, fallido, denegado o bloqueado): HU-42 esc. 1
  exige registrar el resultado de cada operación sensible. Es NULLABLE y sin
  valor por defecto a propósito: audit_logs es de solo anexado (0018) y las
  filas anteriores no se reescriben; la API deriva su resultado de la acción
  (`resultado_de_accion`). Las filas nuevas lo guardan explícito.
- device_config_history: sin UPDATE ni DELETE (HU-49 esc. 1 y 3). La
  aplicación nunca modifica ni borra esas filas; el trigger lo garantiza
  también ante un UPDATE manual.

Reversible: el downgrade quita la columna, su índice y su restricción, y los
triggers del historial. Sin modo batch: en SQLite recrear la tabla perdería
sus triggers de solo anexado.

Revision ID: 0039_auditoria_resultado
Revises: 0038_codigos_verificacion
Create Date: 2026-11-14

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0039_auditoria_resultado"
down_revision: str | None = "0038_codigos_verificacion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RESULTADOS = "('exitoso', 'fallido', 'denegado', 'bloqueado')"

# Una sentencia por llamada: asyncpg no admite varios comandos en un `execute`.
_PG_UPGRADE = [
    """
    CREATE OR REPLACE FUNCTION device_config_history_solo_anexado() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'device_config_history es de solo anexado (HU-49): % no permitido', TG_OP
            USING ERRCODE = 'restrict_violation';
    END
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER device_config_history_solo_anexado
        BEFORE UPDATE OR DELETE ON device_config_history
        FOR EACH ROW EXECUTE FUNCTION device_config_history_solo_anexado()
    """,
]
_PG_DOWNGRADE = [
    "DROP TRIGGER IF EXISTS device_config_history_solo_anexado ON device_config_history",
    "DROP FUNCTION IF EXISTS device_config_history_solo_anexado()",
]
_RECHAZO_SQLITE = "SELECT RAISE(ABORT, 'device_config_history es de solo anexado')"
_TRIGGERS_SQLITE = {
    "device_config_history_solo_anexado_upd": "BEFORE UPDATE ON device_config_history",
    "device_config_history_solo_anexado_del": "BEFORE DELETE ON device_config_history",
}


def _dialecto() -> str:
    return op.get_bind().dialect.name


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("resultado", sa.String(20), nullable=True))
    if _dialecto() == "postgresql":
        # Las filas existentes quedan en NULL: la restricción no exige reescribirlas.
        op.create_check_constraint(
            "ck_audit_logs_resultado", "audit_logs", f"resultado IS NULL OR resultado IN {_RESULTADOS}"
        )
    op.create_index("idx_audit_logs_resultado_created", "audit_logs", ["resultado", "created_at"])

    if _dialecto() == "postgresql":
        for sentencia in _PG_UPGRADE:
            op.execute(sentencia)
    elif _dialecto() == "sqlite":
        for nombre, evento in _TRIGGERS_SQLITE.items():
            op.execute(f"CREATE TRIGGER {nombre} {evento} BEGIN {_RECHAZO_SQLITE}; END")


def downgrade() -> None:
    if _dialecto() == "postgresql":
        for sentencia in _PG_DOWNGRADE:
            op.execute(sentencia)
    elif _dialecto() == "sqlite":
        for nombre in _TRIGGERS_SQLITE:
            op.execute(f"DROP TRIGGER IF EXISTS {nombre}")

    op.drop_index("idx_audit_logs_resultado_created", table_name="audit_logs")
    if _dialecto() == "postgresql":
        op.drop_constraint("ck_audit_logs_resultado", "audit_logs", type_="check")
    op.drop_column("audit_logs", "resultado")
