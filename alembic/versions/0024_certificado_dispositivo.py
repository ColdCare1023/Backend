"""IOT-006: vínculo del dispositivo con su certificado de cliente (TLS mutuo).

Cuando un nodo se autentica por primera vez con un certificado cuyo CN es su
device_id, `exige_certificado` pasa a verdadero y desde entonces el token
solo ya no basta: quien copie el token no puede conectarse sin la clave
privada que vive en el nodo.

Revision ID: 0024_certificado_dispositivo
Revises: 0023_firmware_ota_real
Create Date: 2026-10-21

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024_certificado_dispositivo"
down_revision: str | None = "0023_firmware_ota_real"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(
            sa.Column("exige_certificado", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("exige_certificado")
