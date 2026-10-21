"""BE-001: procedencia de cada lectura (telemetría del nodo vs. registro manual).

Hasta ahora una lectura enviada por un usuario a través de la API REST era
indistinguible de la telemetría del dispositivo: no guardaba autor y podía
ocupar la identidad (device_id, timestamp) o (device_id, boot_id, seq_no) de
una lectura real del nodo. Desde esta migración cada lectura declara su origen
y, si es manual, quién la registró; el índice único legado solo aplica a la
telemetría de dispositivo.

Revision ID: 0017_origen_lecturas
Revises: 0016_reanclaje_lecturas_sin_ntp
Create Date: 2026-10-21

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_origen_lecturas"
down_revision: str | None = "0016_reanclaje_lecturas_sin_ntp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDICE_LEGADO = "uq_thermal_readings_device_timestamp_legado"
FILTRO_ANTERIOR = "boot_id IS NULL OR seq_no IS NULL"
FILTRO_NUEVO = "(boot_id IS NULL OR seq_no IS NULL) AND origen = 'dispositivo'"


def upgrade() -> None:
    with op.batch_alter_table("thermal_readings") as batch:
        batch.add_column(
            sa.Column("origen", sa.String(20), nullable=False, server_default="dispositivo")
        )
        batch.add_column(sa.Column("registrado_por", sa.Uuid(as_uuid=True), nullable=True))
        batch.create_foreign_key("fk_thermal_readings_registrado_por", "users", ["registrado_por"], ["id"])
    op.drop_index(INDICE_LEGADO, table_name="thermal_readings")
    op.create_index(
        INDICE_LEGADO,
        "thermal_readings",
        ["device_id", "timestamp"],
        unique=True,
        sqlite_where=sa.text(FILTRO_NUEVO),
        postgresql_where=sa.text(FILTRO_NUEVO),
    )


def downgrade() -> None:
    op.drop_index(INDICE_LEGADO, table_name="thermal_readings")
    op.create_index(
        INDICE_LEGADO,
        "thermal_readings",
        ["device_id", "timestamp"],
        unique=True,
        sqlite_where=sa.text(FILTRO_ANTERIOR),
        postgresql_where=sa.text(FILTRO_ANTERIOR),
    )
    with op.batch_alter_table("thermal_readings") as batch:
        batch.drop_constraint("fk_thermal_readings_registrado_por", type_="foreignkey")
        batch.drop_column("registrado_por")
        batch.drop_column("origen")
