"""HU-46: métricas de evaluación, horizonte y particiones de cada versión.

Una versión del modelo ya no puede activarse solo con su hash: debe traer las
métricas con las que se evaluó (los dos juegos de criterios), el horizonte H
de la etiqueta, la versión del esquema de features (ya existía) y las
particiones usadas. La activación se bloquea si falta algo o si no cumple el
criterio principal (IA_CRITERIO_PRINCIPAL).

Revision ID: 0031_hu46_metricas_version_modelo
Revises: 0030_hu54_incidentes_sin_datos
Create Date: 2026-10-26

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0031_hu46_metricas_version_modelo"
down_revision: str | None = "0030_hu54_incidentes_sin_datos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("ai_model_versions") as batch:
        batch.add_column(sa.Column("horizonte_min", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("metricas", _JSON, nullable=True))
        batch.add_column(sa.Column("particiones", _JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ai_model_versions") as batch:
        batch.drop_column("particiones")
        batch.drop_column("metricas")
        batch.drop_column("horizonte_min")
