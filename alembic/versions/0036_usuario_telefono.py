"""Celular del usuario (E.164) para el aviso por SMS al crear la cuenta.

Revision ID: 0036_usuario_telefono
Revises: 0035_alerta_evidencia_excursion
Create Date: 2026-11-01

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0036_usuario_telefono"
down_revision: str | None = "0035_alerta_evidencia_excursion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("telefono", sa.String(20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("telefono")
