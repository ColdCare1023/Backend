"""Predicciones del modelo preventivo temporal, separadas de las mediciones.

Cada fila es la salida del modelo preventivo para una lectura (modo sombra o
activo): estado de calidad, clase, puntuación, umbral, versión y SHA-256 del
artefacto. Es una tabla de evidencia de solo anexado, igual que
`audit_logs` (BE-007): sin UPDATE ni DELETE.

No modifica ninguna tabla existente.

Revision ID: 0025_predicciones_preventivas
Revises: 0024_certificado_dispositivo
Create Date: 2026-10-21

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from alembic import op

JSONVariant = JSON().with_variant(JSONB(), "postgresql")

revision: str = "0025_predicciones_preventivas"
down_revision: str | None = "0024_certificado_dispositivo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLA = "predicciones_preventivas"
_RECHAZO_SQLITE = "SELECT RAISE(ABORT, 'tabla de evidencia de solo anexado')"


def _dialecto() -> str:
    return op.get_bind().dialect.name


def upgrade() -> None:
    op.create_table(
        _TABLA,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("lectura_id", sa.Uuid(as_uuid=True), sa.ForeignKey("thermal_readings.id"), nullable=False),
        sa.Column("device_id", sa.String(50), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("timestamp_inferencia", sa.DateTime(timezone=True), nullable=False),
        sa.Column("modo", sa.String(20), nullable=False),
        sa.Column("estado", sa.String(30), nullable=False),
        sa.Column("horizonte_min", sa.Integer(), nullable=True),
        sa.Column("clase", sa.String(30), nullable=True),
        sa.Column("puntuacion", sa.Float(), nullable=True),
        sa.Column("umbral", sa.Float(), nullable=True),
        sa.Column("puntuacion_calibrada", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("modelo_version", sa.String(64), nullable=True),
        sa.Column("modelo_sha256", sa.String(64), nullable=True),
        sa.Column("motivo", sa.String(200), nullable=True),
        sa.Column("vector_features", JSONVariant, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_predicciones_preventivas_lectura_id", _TABLA, ["lectura_id"])
    op.create_index("ix_predicciones_preventivas_device_id", _TABLA, ["device_id"])
    op.create_index("ix_predicciones_preventivas_timestamp_inferencia", _TABLA, ["timestamp_inferencia"])
    if _dialecto() == "postgresql":
        # evidencia_rechazar_cambio() existe desde la migración 0018.
        op.execute(
            f"CREATE TRIGGER {_TABLA}_solo_anexado BEFORE UPDATE OR DELETE ON {_TABLA} "
            "FOR EACH ROW EXECUTE FUNCTION evidencia_rechazar_cambio()"
        )
    elif _dialecto() == "sqlite":
        for operacion in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER {_TABLA}_solo_anexado_{operacion.lower()} BEFORE {operacion} ON {_TABLA} "
                f"BEGIN {_RECHAZO_SQLITE}; END"
            )


def downgrade() -> None:
    if _dialecto() == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {_TABLA}_solo_anexado ON {_TABLA}")
    elif _dialecto() == "sqlite":
        for operacion in ("update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS {_TABLA}_solo_anexado_{operacion}")
    op.drop_index("ix_predicciones_preventivas_timestamp_inferencia", table_name=_TABLA)
    op.drop_index("ix_predicciones_preventivas_device_id", table_name=_TABLA)
    op.drop_index("ix_predicciones_preventivas_lectura_id", table_name=_TABLA)
    op.drop_table(_TABLA)
