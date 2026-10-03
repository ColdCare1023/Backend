"""HU-11: la identidad de una lectura es (device_id, boot_id, seq_no), no su timestamp.

Sin NTP (reinicio estando offline) el reloj del nodo arranca en la misma hora en
cada arranque: dos arranques producen los mismos timestamps y la restricción
única (device_id, timestamp) hacía chocar lecturas distintas — el INSERT
fallaba, el backend no acusaba y el nodo reintentaba para siempre. La
restricción queda solo para filas de firmware anterior que no declara
boot_id/seq_no; para el resto ya existe el índice único de la tripleta (0013).

Revision ID: 0015_identidad_boot_seq
Revises: 0014_hu53_hu54_responsable_dispositivo
Create Date: 2026-10-02

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_identidad_boot_seq"
down_revision: str | None = "0014_hu53_hu54_responsable_dispositivo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDICE_LEGADO = "uq_thermal_readings_device_timestamp_legado"
FILTRO_LEGADO = "boot_id IS NULL OR seq_no IS NULL"


def upgrade() -> None:
    with op.batch_alter_table("thermal_readings") as batch:
        batch.drop_constraint("uq_thermal_readings_device_timestamp", type_="unique")
    op.create_index(
        INDICE_LEGADO,
        "thermal_readings",
        ["device_id", "timestamp"],
        unique=True,
        sqlite_where=sa.text(FILTRO_LEGADO),
        postgresql_where=sa.text(FILTRO_LEGADO),
    )


def downgrade() -> None:
    op.drop_index(INDICE_LEGADO, table_name="thermal_readings")
    with op.batch_alter_table("thermal_readings") as batch:
        batch.create_unique_constraint("uq_thermal_readings_device_timestamp", ["device_id", "timestamp"])
