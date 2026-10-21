"""BE-009: revocación de tokens persistente y común a todas las instancias.

La lista de JWT revocados y los tickets SSE consumidos vivían en memoria del
proceso, acotados por expulsión LRU: un reinicio, otra instancia o el simple
desplazamiento de la lista devolvía la validez a un token cerrado con logout.

Revision ID: 0019_jti_invalidados
Revises: 0018_evidencia_solo_anexado
Create Date: 2026-10-21

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_jti_invalidados"
down_revision: str | None = "0018_evidencia_solo_anexado"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jti_invalidados",
        sa.Column("jti", sa.String(64), primary_key=True),
        sa.Column("motivo", sa.String(30), nullable=False),
        sa.Column("expira_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_jti_invalidados_expira_en", "jti_invalidados", ["expira_en"])
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("tokens_invalidos_antes_de", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("tokens_invalidos_antes_de")
    op.drop_index("ix_jti_invalidados_expira_en", table_name="jti_invalidados")
    op.drop_table("jti_invalidados")
