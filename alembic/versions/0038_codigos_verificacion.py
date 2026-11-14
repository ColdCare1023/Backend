"""Códigos de verificación de un solo uso enviados por correo.

- codigos_verificacion: segundo factor del login (OTP_LOGIN_ENABLED),
  recuperación de contraseña por código y activación de cuentas creadas por un
  administrador. Solo se guarda el HMAC-SHA256 del código (CODIGOS_HMAC_KEY) y
  el SHA-256 del desafío opaco del login; los intentos fallidos se persisten
  y los códigos anulados se conservan un tiempo para el tope por cuenta.
- ix_users_email_lower: índice de expresión para buscar el correo sin
  distinguir mayúsculas.

Revision ID: 0038_codigos_verificacion
Revises: 0037_hu_criterios_pendientes
Create Date: 2026-11-14

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0038_codigos_verificacion"
down_revision: str | None = "0037_hu_criterios_pendientes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "codigos_verificacion",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("proposito", sa.String(20), nullable=False),
        sa.Column("codigo_hash", sa.String(64), nullable=False),
        # Único solo cuando existe: los códigos de recuperación y activación no llevan desafío.
        sa.Column("desafio_hash", sa.String(64), nullable=True, unique=True),
        sa.Column("expira_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("intentos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("usado_en", sa.DateTime(timezone=True), nullable=True),
        # creado_en: último envío (el reenvío lo mueve); emitido_en: inmutable.
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("emitido_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.CheckConstraint(
            "proposito IN ('login', 'recuperacion', 'activacion')", name="ck_codigos_verificacion_proposito"
        ),
    )
    op.create_index(
        "ix_codigos_verificacion_usuario_proposito", "codigos_verificacion", ["usuario_id", "proposito"]
    )
    # Como mucho un código vivo por usuario y propósito. Los anulados se
    # conservan (usado_en) para el tope de fallos por cuenta.
    op.create_index(
        "uq_codigos_verificacion_vigente",
        "codigos_verificacion",
        ["usuario_id", "proposito"],
        unique=True,
        postgresql_where=sa.text("usado_en IS NULL"),
        sqlite_where=sa.text("usado_en IS NULL"),
    )
    # El login y la recuperación buscan el correo con lower(email). No es UNIQUE:
    # una base heredada podría tener dos correos que solo difieren en mayúsculas.
    op.create_index("ix_users_email_lower", "users", [sa.text("lower(email)")])


def downgrade() -> None:
    op.drop_index("ix_users_email_lower", table_name="users")
    op.drop_index("uq_codigos_verificacion_vigente", table_name="codigos_verificacion")
    op.drop_index("ix_codigos_verificacion_usuario_proposito", table_name="codigos_verificacion")
    op.drop_table("codigos_verificacion")
