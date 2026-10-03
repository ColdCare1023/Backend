"""HU-23 (máquina de estados PENDIENTE/RECONOCIDA/ATENDIDA de alertas
críticas) y HU-27/HU-28 (rectificación de acciones correctivas) del backlog
de 51 HU finales (2026-08-30).

No modifica 0001..0007.

Revision ID: 0008_51hu_hu23_hu27_hu28
Revises: 0007_51hu_fase1_nucleo
Create Date: 2026-09-01

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_51hu_hu23_hu27_hu28"
down_revision: str | None = "0007_51hu_fase1_nucleo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── HU-23: máquina de estados de acuse de alertas críticas ────────────
    op.add_column(
        "thermal_alerts",
        sa.Column("estado", sa.String(20), nullable=False, server_default="pendiente"),
    )
    op.add_column("thermal_alerts", sa.Column("reconocida_en", sa.DateTime(timezone=True), nullable=True))
    op.add_column("thermal_alerts", sa.Column("atendida_en", sa.DateTime(timezone=True), nullable=True))
    # Datos existentes: una alerta ya `revisada` se considera al menos
    # reconocida (no hay forma de saber retroactivamente si fue "atendida").
    op.execute(
        sa.text(
            "UPDATE thermal_alerts SET estado = 'reconocida' WHERE revisada = true OR revisada = 1"
        )
    )

    # ── HU-28: rectificación de acciones correctivas ───────────────────────
    op.add_column(
        "corrective_actions",
        sa.Column("corrige_accion_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    # batch_alter_table: SQLite no soporta ALTER de restricciones. En
    # PostgreSQL emite el mismo ALTER TABLE ... ADD CONSTRAINT.
    with op.batch_alter_table("corrective_actions") as batch:
        batch.create_foreign_key(
            "fk_corrective_actions_corrige_accion_id",
            "corrective_actions",
            ["corrige_accion_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("corrective_actions") as batch:
        batch.drop_constraint("fk_corrective_actions_corrige_accion_id", type_="foreignkey")
        batch.drop_column("corrige_accion_id")

    op.drop_column("thermal_alerts", "atendida_en")
    op.drop_column("thermal_alerts", "reconocida_en")
    op.drop_column("thermal_alerts", "estado")
