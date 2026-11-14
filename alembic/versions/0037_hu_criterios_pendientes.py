"""Campos que exigen HU-22, HU-27 y HU-30.

- thermal_readings.inferido_en: hora real de la inferencia (HU-22 esc. 2).
- corrective_actions.decision y justificacion: la acción correctiva guarda
  decisión, acción ejecutada (descripcion) y justificación (HU-27 esc. 1).
- devices.margen_aviso_calibracion_dias: margen de aviso configurable por
  equipo (HU-30 esc. 2 y 4).

Revision ID: 0037_hu_criterios_pendientes
Revises: 0036_usuario_telefono
Create Date: 2026-11-08

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0037_hu_criterios_pendientes"
down_revision: str | None = "0036_usuario_telefono"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("thermal_readings", sa.Column("inferido_en", sa.DateTime(timezone=True), nullable=True))
    op.add_column("corrective_actions", sa.Column("decision", sa.Text(), nullable=True))
    op.add_column("corrective_actions", sa.Column("justificacion", sa.Text(), nullable=True))
    op.add_column(
        "devices",
        sa.Column("margen_aviso_calibracion_dias", sa.Integer(), nullable=False, server_default="30"),
    )


def downgrade() -> None:
    # Sin modo batch: en SQLite recrear la tabla perdería sus triggers de solo anexado.
    op.drop_column("devices", "margen_aviso_calibracion_dias")
    op.drop_column("corrective_actions", "justificacion")
    op.drop_column("corrective_actions", "decision")
    op.drop_column("thermal_readings", "inferido_en")
