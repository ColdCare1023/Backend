"""HU-43/HU-44: operación pendiente en el broker, persistente por dispositivo.

Con EMQX Cloud Serverless (`BROKER_USUARIOS_MODO=manual`) el usuario del nodo
se crea o retira a mano en la consola. Hasta ahora eso solo se avisaba en la
respuesta de la rotación; si el administrador cerraba la pantalla, no quedaba
rastro de que la consola seguía sin actualizar. Estas columnas guardan la
operación pendiente (`aprovisionar` o `retirar`), desde cuándo, quién la
originó y por qué, y quién confirmó después que la hizo en la consola.

Revision ID: 0029_hu43_hu44_pendiente_broker
Revises: 0028_hu28_acciones_append_only
Create Date: 2026-10-26

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0029_hu43_hu44_pendiente_broker"
down_revision: str | None = "0028_hu28_acciones_append_only"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("broker_pendiente", sa.String(20), nullable=True))
        batch.add_column(sa.Column("broker_pendiente_motivo", sa.String(40), nullable=True))
        batch.add_column(sa.Column("broker_pendiente_desde", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column(
                "broker_pendiente_por",
                sa.Uuid(),
                sa.ForeignKey("users.id", name="fk_devices_broker_pendiente_por_users"),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("broker_confirmado_en", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column(
                "broker_confirmado_por",
                sa.Uuid(),
                sa.ForeignKey("users.id", name="fk_devices_broker_confirmado_por_users"),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("broker_confirmado_por")
        batch.drop_column("broker_confirmado_en")
        batch.drop_column("broker_pendiente_por")
        batch.drop_column("broker_pendiente_desde")
        batch.drop_column("broker_pendiente_motivo")
        batch.drop_column("broker_pendiente")
