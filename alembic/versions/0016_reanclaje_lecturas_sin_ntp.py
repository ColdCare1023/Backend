"""HU-06: reanclaje de la hora de lecturas capturadas sin NTP.

`uptime_ms` (millis() del nodo al capturar) permite reconstruir la hora real de
las lecturas `unsynced` de un arranque en cuanto llega una `synced` del mismo
arranque; el resultado va en `timestamp_estimado` y NUNCA sobrescribe
`timestamp`, que es lo que declaró el nodo y forma parte de la evidencia.

Revision ID: 0016_reanclaje_lecturas_sin_ntp
Revises: 0015_identidad_boot_seq
Create Date: 2026-10-02

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_reanclaje_lecturas_sin_ntp"
down_revision: str | None = "0015_identidad_boot_seq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("thermal_readings") as batch:
        batch.add_column(sa.Column("uptime_ms", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("timestamp_estimado", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("thermal_readings") as batch:
        batch.drop_column("timestamp_estimado")
        batch.drop_column("uptime_ms")
