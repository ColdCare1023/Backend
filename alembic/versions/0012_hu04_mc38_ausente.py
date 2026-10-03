"""HU-04: permite declarar que un dispositivo no tiene MC-38 instalado.

`thermal_readings.apertura_refrigerador` era NOT NULL: un nodo sin MC-38
no tenía forma de reportar "no aplica" sin simular una puerta cerrada que
en realidad no existe. Se relaja a NULLABLE; `apertura_refrigerador IS NULL`
es en sí mismo la señal de "sin MC-38" (`mc38_status` del payload MQTT no
se persiste aparte porque sería redundante con ese NULL).

No modifica 0001..0011.

Revision ID: 0012_hu04_mc38_ausente
Revises: 0011_51hu_hu43_hu49_hu51_dispositivos
Create Date: 2026-09-02

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_hu04_mc38_ausente"
down_revision: str | None = "0011_51hu_hu43_hu49_hu51_dispositivos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("thermal_readings") as batch:
        batch.alter_column(
            "apertura_refrigerador", existing_type=sa.Boolean(), nullable=True
        )


def downgrade() -> None:
    with op.batch_alter_table("thermal_readings") as batch:
        batch.alter_column(
            "apertura_refrigerador",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        )
