"""persistencia de version/confianza/origen del modelo IA por lectura

Corrige el hallazgo AI-06 de la auditoría de IA: sin esta columna era
imposible determinar retroactivamente con qué versión del modelo se
clasificó una lectura histórica si el modelo se reentrena/reemplaza.

Revision ID: 0003_lecturas_modelo_ia
Revises: 0002_thermal_readings_dedup
Create Date: 2026-07-22

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_lecturas_modelo_ia"
down_revision: str | None = "0002_thermal_readings_dedup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "thermal_readings", sa.Column("modelo_version", sa.String(50), nullable=True)
    )
    op.add_column(
        "thermal_readings", sa.Column("confianza_ia", sa.Float(), nullable=True)
    )
    op.add_column(
        "thermal_readings", sa.Column("origen_clasificacion", sa.String(30), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("thermal_readings", "origen_clasificacion")
    op.drop_column("thermal_readings", "confianza_ia")
    op.drop_column("thermal_readings", "modelo_version")
