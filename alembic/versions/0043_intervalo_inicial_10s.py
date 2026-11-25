"""HU-01 y HU-54. Intervalo inicial de las altas nuevas desde el perfil de 10 s."""

import sqlalchemy as sa

from alembic import op

revision = "0043_intervalo_inicial_10s"
down_revision = "0042_bigint_identidad"
branch_labels = None
depends_on = None


def _intervalo_inicial(segundos: int) -> None:
    with op.batch_alter_table("devices") as batch:
        batch.alter_column(
            "intervalo_reporte_segundos",
            existing_type=sa.Integer(),
            existing_nullable=False,
            server_default=str(segundos),
        )


def upgrade() -> None:
    _intervalo_inicial(10)


def downgrade() -> None:
    _intervalo_inicial(30)
