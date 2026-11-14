"""HU-48: último estado_nodo por dispositivo y episodios de buffer saturado.

- `estado_nodo_dispositivo`: una fila por nodo con el último `estado_nodo`
  aceptado (pendientes en LittleFS, capacidad, ocupación, pérdidas,
  cuarentena y, si el firmware lo informa, el rango temporal de los
  pendientes). Se sobrescribe en cada latido: es telemetría de salud, no
  evidencia, y no entra en la cadena de hash.
- `episodios_buffer_saturado`: un registro por episodio de capacidad agotada
  (desde, hasta, causa, pérdidas), identificado por la primera muestra
  perdida. Los `buffer_saturado` siguientes del mismo episodio lo actualizan.

Solo crea tablas nuevas: no toca datos existentes y el downgrade las borra.
La auditoría de `buffer_saturado` en `audit_logs` y en la cadena sigue igual.

Revision ID: 0040_estado_nodo
Revises: 0039_auditoria_resultado
Create Date: 2026-11-14

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0040_estado_nodo"
down_revision: str | None = "0039_auditoria_resultado"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONVariant = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "estado_nodo_dispositivo",
        sa.Column("device_id", sa.String(50), sa.ForeignKey("devices.id"), primary_key=True),
        sa.Column("recibido_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ocurrido_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("boot_id", sa.BigInteger(), nullable=True),
        sa.Column("pendientes", sa.Integer(), nullable=True),
        sa.Column("capacidad", sa.Integer(), nullable=True),
        sa.Column("ocupacion_pct", sa.Integer(), nullable=True),
        sa.Column("perdidas_total", sa.BigInteger(), nullable=True),
        sa.Column("cuarentena", sa.Integer(), nullable=True),
        sa.Column("pendientes_desde", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pendientes_hasta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultima_sincronizacion_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detalle", JSONVariant, nullable=True),
    )

    op.create_table(
        "episodios_buffer_saturado",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device_id", sa.String(50), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("desde", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hasta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("desde_boot_id", sa.BigInteger(), nullable=True),
        sa.Column("desde_seq_no", sa.BigInteger(), nullable=True),
        sa.Column("hasta_boot_id", sa.BigInteger(), nullable=True),
        sa.Column("hasta_seq_no", sa.BigInteger(), nullable=True),
        sa.Column("causa", sa.String(40), nullable=False),
        sa.Column("perdidas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("perdidas_total", sa.BigInteger(), nullable=True),
        sa.Column("abierto", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("registrado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cerrado_en", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_episodios_buffer_saturado_device_id", "episodios_buffer_saturado", ["device_id"])
    op.create_index(
        "uq_episodios_buffer_saturado_inicio",
        "episodios_buffer_saturado",
        ["device_id", "desde_boot_id", "desde_seq_no"],
        unique=True,
        postgresql_where=sa.text("desde_boot_id IS NOT NULL AND desde_seq_no IS NOT NULL"),
        sqlite_where=sa.text("desde_boot_id IS NOT NULL AND desde_seq_no IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_episodios_buffer_saturado_inicio", table_name="episodios_buffer_saturado")
    op.drop_index("ix_episodios_buffer_saturado_device_id", table_name="episodios_buffer_saturado")
    op.drop_table("episodios_buffer_saturado")
    op.drop_table("estado_nodo_dispositivo")
