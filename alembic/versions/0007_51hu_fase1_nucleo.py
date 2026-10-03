"""HU-05 (schema_version + reading_id de telemetría), HU-18/HU-21/HU-34
(separación de model_class / excursion_confirmada / riesgo_efectivo) y HU-41
(rol AUDITOR) del backlog final de 51 historias de usuario (2026-08-30).

No modifica 0001..0006.

Revision ID: 0007_51hu_fase1_nucleo
Revises: 0006_checklist_calibracion_indices
Create Date: 2026-09-01

"""
import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_51hu_fase1_nucleo"
down_revision: str | None = "0006_checklist_calibracion_indices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── HU-05: identidad lógica y versión de contrato de la telemetría ─────
    op.add_column("thermal_readings", sa.Column("reading_id", sa.String(100), nullable=True))
    op.create_index("ix_thermal_readings_reading_id", "thermal_readings", ["reading_id"])
    op.add_column("thermal_readings", sa.Column("schema_version", sa.Integer(), nullable=True))

    # ── HU-18/HU-21/HU-34: model_class (nivel_riesgo, ya existente) vs.
    # excursion_confirmada (regla directa 2-8 °C, inmediata e independiente
    # de la IA) vs. riesgo_efectivo (política determinista que combina
    # ambas). No se colapsan en un único campo — Observación 5 del backlog.
    op.add_column(
        "thermal_readings",
        sa.Column("excursion_confirmada", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("thermal_readings", sa.Column("riesgo_efectivo", sa.String(30), nullable=True))

    # ── HU-41: rol "Responsable de auditoría", usado como rol narrativo en
    # HU-26/28/37/42/47/49/50 pero ausente del enum hasta esta historia.
    roles_table = sa.table("roles", sa.column("id", sa.Uuid()), sa.column("nombre", sa.String()))
    op.bulk_insert(roles_table, [{"id": uuid.uuid4(), "nombre": "auditor"}])


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM roles WHERE nombre = 'auditor'"))

    op.drop_column("thermal_readings", "riesgo_efectivo")
    op.drop_column("thermal_readings", "excursion_confirmada")
    op.drop_column("thermal_readings", "schema_version")
    op.drop_index("ix_thermal_readings_reading_id", table_name="thermal_readings")
    op.drop_column("thermal_readings", "reading_id")
