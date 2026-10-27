"""HU-44: rotación por compromiso con la consola del broker pendiente.

En modo manual (EMQX Serverless) el broker sigue aceptando el token anterior
hasta que alguien edite la consola. Si la rotación se hizo porque ese token se
filtró, quien lo tenga publica con el mismo `device_id` y el backend no puede
distinguir con qué token se autenticó cada conexión. Esta marca indica que la
rotación fue por compromiso: mientras la operación siga pendiente, la ingesta
retiene la telemetría del nodo SIN acuse (el nodo legítimo la conserva en
LittleFS y la reenvía cuando el administrador confirma la consola).

Revision ID: 0032_hu44_rotacion_compromiso
Revises: 0031_hu46_metricas_version_modelo
Create Date: 2026-10-27

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0032_hu44_rotacion_compromiso"
down_revision: str | None = "0031_hu46_metricas_version_modelo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(
            sa.Column("rotacion_por_compromiso", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("rotacion_por_compromiso")
