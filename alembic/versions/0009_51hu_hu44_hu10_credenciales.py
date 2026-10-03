"""HU-44/HU-10 (backlog de 51 HU finales, 2026-08-30): credencial MQTT por
dispositivo (hash, nunca en claro) con rotación y revocación auditables.

No modifica 0001..0008.

Revision ID: 0009_51hu_hu44_hu10_credenciales
Revises: 0008_51hu_hu23_hu27_hu28
Create Date: 2026-09-01

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_51hu_hu44_hu10_credenciales"
down_revision: str | None = "0008_51hu_hu23_hu27_hu28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("mqtt_token_hash", sa.String(255), nullable=True))
    op.add_column(
        "devices",
        sa.Column("mqtt_token_activo", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "devices", sa.Column("mqtt_credencial_actualizada_en", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("devices", "mqtt_credencial_actualizada_en")
    op.drop_column("devices", "mqtt_token_activo")
    op.drop_column("devices", "mqtt_token_hash")
