"""Evidencia completa de la excursión en la alerta.

`iniciada_en` toma la hora de CAPTURA de la primera lectura (no la de llegada:
las lecturas del buffer del nodo pueden llegar horas tarde), `temperatura_pico`
guarda la más alejada de 2-8 °C y `historica` marca el episodio detectado con
lecturas diferidas.

Revision ID: 0035_alerta_evidencia_excursion
Revises: 0034_hu44_expulsion_sesion
Create Date: 2026-11-01

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0035_alerta_evidencia_excursion"
down_revision: str | None = "0034_hu44_expulsion_sesion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("thermal_alerts") as batch:
        batch.add_column(sa.Column("iniciada_en", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("temperatura_pico", sa.Float(), nullable=True))
        batch.add_column(sa.Column("historica", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("thermal_alerts") as batch:
        batch.drop_column("historica")
        batch.drop_column("temperatura_pico")
        batch.drop_column("iniciada_en")
