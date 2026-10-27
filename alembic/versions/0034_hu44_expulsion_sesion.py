"""HU-44: motivo de la última expulsión de sesión hecha por el backend.

Cada rotación (y la confirmación de la consola) expulsa la sesión MQTT del
nodo; el broker publica entonces el LWT y el nodo vuelve con `online`. Sin
contexto, la bitácora muestra ese par como una caída del nodo. Con la hora y el
motivo de la expulsión, el LWT y el `online` que llegan poco después se
auditan como «reconexión por rotación».

Revision ID: 0034_hu44_expulsion_sesion
Revises: 0033_hu44_prueba_posesion
Create Date: 2026-10-27

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0034_hu44_expulsion_sesion"
down_revision: str | None = "0033_hu44_prueba_posesion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("sesion_expulsada_en", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("sesion_expulsada_motivo", sa.String(40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("sesion_expulsada_motivo")
        batch.drop_column("sesion_expulsada_en")
