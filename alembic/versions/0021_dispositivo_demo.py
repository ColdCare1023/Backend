"""BE-015: marca de dispositivo de demostración.

Las lecturas `simulated` del firmware de demostración solo se aceptan de un
dispositivo dado de alta explícitamente como demo.

Revision ID: 0021_dispositivo_demo
Revises: 0020_consentimiento_no_presunto
Create Date: 2026-10-21

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021_dispositivo_demo"
down_revision: str | None = "0020_consentimiento_no_presunto"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("es_demo", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("es_demo")
