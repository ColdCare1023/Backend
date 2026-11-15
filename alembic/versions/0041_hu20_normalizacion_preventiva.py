"""HU-20 esc. 3: ventana de normalización de la alerta preventiva.

`thermal_alerts.normalizacion_desde` guarda la hora de captura de la primera
lectura Normal tras la última preventiva del episodio. El episodio preventivo
se resuelve solo cuando el riesgo lleva ALERTA_PREVENTIVA_MINUTOS_NORMALES
seguidos en Normal. Columna nula: las alertas existentes quedan igual.

Revision ID: 0041_hu20_normalizacion_preventiva
Revises: 0040_estado_nodo
Create Date: 2026-11-14

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0041_hu20_normalizacion_preventiva"
down_revision: str | None = "0040_estado_nodo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("thermal_alerts") as batch:
        batch.add_column(sa.Column("normalizacion_desde", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("thermal_alerts") as batch:
        batch.drop_column("normalizacion_desde")
