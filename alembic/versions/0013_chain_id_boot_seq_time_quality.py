"""Backlog 54 HU — modelo de datos base (HU-01/05/11/24/25).

- traceability_records: chain_id + chain_seq (una cadena de hash SHA-256 por
  unidad monitoreada en vez de una única cadena global) y hash_version (para
  no recalcular retroactivamente registros ya persistidos: ver
  src/domain/value_objects/hash_encadenado.py). Backfill: chain_id =
  device_id si existe, si no 'SISTEMA'; chain_seq = posición secuencial
  dentro de esa cadena en orden de inserción (created_at, id); hash_version=1
  para todo lo existente (fórmula sin chain_id).
- thermal_readings: boot_id, seq_no (idempotencia real HU-11), time_quality
  (HU-01) y received_at (HU-05, instante de recepción del backend, distinto
  de `timestamp`=captured_at). NULL en filas existentes y en payloads de
  firmware que aún no los declare.

Revision ID: 0013_chain_id_boot_seq_time_quality
Revises: 0012_hu04_mc38_ausente
Create Date: 2026-09-18

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_chain_id_boot_seq_time_quality"
down_revision: str | None = "0012_hu04_mc38_ausente"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    with op.batch_alter_table("traceability_records") as batch:
        batch.add_column(
            sa.Column("chain_id", sa.String(80), nullable=False, server_default="SISTEMA")
        )
        batch.add_column(sa.Column("chain_seq", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("hash_version", sa.Integer(), nullable=False, server_default="1")
        )

    # Backfill chain_id real (device_id si existe) y chain_seq por cadena, en
    # el mismo orden de inserción que usa el camino de escritura/verificación
    # (created_at, id). Se hace en Python (no SQL de ventana) para ser
    # portable entre PostgreSQL y SQLite (usado en tests) sin duplicar lógica.
    metadata = sa.MetaData()
    tabla = sa.Table("traceability_records", metadata, autoload_with=bind)
    filas = bind.execute(
        sa.select(tabla.c.id, tabla.c.device_id)
        .order_by(tabla.c.created_at.asc(), tabla.c.id.asc())
    ).all()

    contador_por_cadena: dict[str, int] = {}
    for fila_id, device_id in filas:
        chain_id = device_id or "SISTEMA"
        contador_por_cadena[chain_id] = contador_por_cadena.get(chain_id, 0) + 1
        bind.execute(
            tabla.update()
            .where(tabla.c.id == fila_id)
            .values(chain_id=chain_id, chain_seq=contador_por_cadena[chain_id])
        )

    with op.batch_alter_table("traceability_records") as batch:
        batch.create_unique_constraint("uq_traceability_chain_seq", ["chain_id", "chain_seq"])
        batch.create_index("ix_traceability_records_chain_id", ["chain_id"])

    with op.batch_alter_table("thermal_readings") as batch:
        batch.add_column(sa.Column("boot_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("seq_no", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("time_quality", sa.String(20), nullable=True))
        batch.add_column(sa.Column("received_at", sa.DateTime(timezone=True), nullable=True))

    op.create_index(
        "uq_thermal_readings_device_boot_seq",
        "thermal_readings",
        ["device_id", "boot_id", "seq_no"],
        unique=True,
        postgresql_where=sa.text("boot_id IS NOT NULL AND seq_no IS NOT NULL"),
        sqlite_where=sa.text("boot_id IS NOT NULL AND seq_no IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_thermal_readings_device_boot_seq", table_name="thermal_readings")
    with op.batch_alter_table("thermal_readings") as batch:
        batch.drop_column("received_at")
        batch.drop_column("time_quality")
        batch.drop_column("seq_no")
        batch.drop_column("boot_id")

    with op.batch_alter_table("traceability_records") as batch:
        batch.drop_index("ix_traceability_records_chain_id")
        batch.drop_constraint("uq_traceability_chain_seq", type_="unique")
        batch.drop_column("hash_version")
        batch.drop_column("chain_seq")
        batch.drop_column("chain_id")
