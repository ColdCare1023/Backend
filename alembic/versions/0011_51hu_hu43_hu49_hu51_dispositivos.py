"""HU-43 (alta explícita con sensores habilitados), HU-49 (historial
auditable de configuración) y HU-51 (metadatos de instalación del sensor)
del backlog de 51 HU finales (2026-08-30).

No modifica 0001..0010.

Revision ID: 0011_51hu_hu43_hu49_hu51_dispositivos
Revises: 0010_51hu_hu46_hu47_gobierno_ia
Create Date: 2026-09-02

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from alembic import op

JSONVariant = JSON().with_variant(JSONB(), "postgresql")

revision: str = "0011_51hu_hu43_hu49_hu51_dispositivos"
down_revision: str | None = "0010_51hu_hu46_hu47_gobierno_ia"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── HU-43: metadatos de alta explícita ─────────────────────────────────
    op.add_column("devices", sa.Column("sensores_habilitados", JSONVariant, nullable=True))
    op.add_column("devices", sa.Column("dado_de_alta_por", sa.Uuid(as_uuid=True), nullable=True))

    # ── HU-51: metadatos de instalación del sensor ─────────────────────────
    op.add_column("devices", sa.Column("fecha_instalacion", sa.Date(), nullable=True))
    op.add_column("devices", sa.Column("instalado_por", sa.Uuid(as_uuid=True), nullable=True))
    op.add_column("devices", sa.Column("observaciones_instalacion", sa.Text(), nullable=True))

    with op.batch_alter_table("devices") as batch:
        batch.create_foreign_key("fk_devices_dado_de_alta_por", "users", ["dado_de_alta_por"], ["id"])
        batch.create_foreign_key("fk_devices_instalado_por", "users", ["instalado_por"], ["id"])

    # ── HU-49: historial auditable de configuración ────────────────────────
    op.create_table(
        "device_config_history",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("device_id", sa.String(50), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("campo", sa.String(50), nullable=False),
        sa.Column("valor_anterior", sa.Text(), nullable=True),
        sa.Column("valor_nuevo", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_device_config_history_device_id", "device_config_history", ["device_id"])


def downgrade() -> None:
    op.drop_index("ix_device_config_history_device_id", table_name="device_config_history")
    op.drop_table("device_config_history")

    with op.batch_alter_table("devices") as batch:
        batch.drop_constraint("fk_devices_instalado_por", type_="foreignkey")
        batch.drop_constraint("fk_devices_dado_de_alta_por", type_="foreignkey")

    op.drop_column("devices", "observaciones_instalacion")
    op.drop_column("devices", "instalado_por")
    op.drop_column("devices", "fecha_instalacion")
    op.drop_column("devices", "dado_de_alta_por")
    op.drop_column("devices", "sensores_habilitados")
