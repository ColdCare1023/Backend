"""HU-53/HU-54 (backlog 54 HU): responsable registrado del dispositivo.

Destinatario real de las notificaciones de excursión crítica por correo
(HU-53) y SMS opcional (HU-54) — antes solo existía un destinatario global
fijo (SMTP_TO) para todos los dispositivos, que sigue funcionando como
respaldo cuando un dispositivo no tiene responsable registrado.

Revision ID: 0014_hu53_hu54_responsable_dispositivo
Revises: 0013_chain_id_boot_seq_time_quality
Create Date: 2026-09-18

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_hu53_hu54_responsable_dispositivo"
down_revision: str | None = "0013_chain_id_boot_seq_time_quality"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("responsable_nombre", sa.String(120), nullable=True))
        batch.add_column(sa.Column("responsable_email", sa.String(150), nullable=True))
        batch.add_column(sa.Column("responsable_telefono", sa.String(20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("responsable_telefono")
        batch.drop_column("responsable_email")
        batch.drop_column("responsable_nombre")
