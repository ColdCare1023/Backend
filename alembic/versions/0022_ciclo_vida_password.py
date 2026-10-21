"""BE-025: ciclo de vida de contraseñas.

- `users.debe_cambiar_password`: la contraseña la asignó un administrador
  (alta, restablecimiento, reactivación) y debe cambiarse en el próximo acceso.
- `password_reset_tokens`: enlaces de recuperación de un solo uso; se guarda
  solo el SHA-256 del token.

Revision ID: 0022_ciclo_vida_password
Revises: 0021_dispositivo_demo
Create Date: 2026-10-21

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022_ciclo_vida_password"
down_revision: str | None = "0021_dispositivo_demo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("debe_cambiar_password", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expira_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("usado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_password_reset_tokens_usuario_id", "password_reset_tokens", ["usuario_id"])


def downgrade() -> None:
    op.drop_index("ix_password_reset_tokens_usuario_id", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("debe_cambiar_password")
