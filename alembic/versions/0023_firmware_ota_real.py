"""IOT-004: release de firmware con URL de descarga y tamaño (OTA real).

Con URL y tamaño, ejecutar un despliegue publica una orden OTA verificable
(URL HTTPS, SHA-256, tamaño) para el nodo; sin ellos, solo existe el flujo
simulado de desarrollo (BE-016: no disponible en producción).

Revision ID: 0023_firmware_ota_real
Revises: 0022_ciclo_vida_password
Create Date: 2026-10-21

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023_firmware_ota_real"
down_revision: str | None = "0022_ciclo_vida_password"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("firmware_releases") as batch:
        batch.add_column(sa.Column("url_descarga", sa.String(256), nullable=True))
        batch.add_column(sa.Column("tamano_bytes", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("firmware_releases") as batch:
        batch.drop_column("tamano_bytes")
        batch.drop_column("url_descarga")
