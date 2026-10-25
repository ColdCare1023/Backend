"""HU-13: momento y arranque del último cambio de conectividad del nodo.

`conectividad_cambiada_en` permite mostrar «Desconectado desde HH:MM».
`conectividad_boot_id` es el arranque (boot_id) del último `online` aplicado:
un LWT con boot_id anterior pertenece a una sesión ya superada (el nodo
reinició y volvió) y no debe marcar offline al dispositivo.

Revision ID: 0026_hu13_conectividad_dispositivo
Revises: 0025_predicciones_preventivas
Create Date: 2026-10-08

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026_hu13_conectividad_dispositivo"
down_revision: str | None = "0025_predicciones_preventivas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("conectividad_cambiada_en", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("conectividad_boot_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("conectividad_boot_id")
        batch.drop_column("conectividad_cambiada_en")
