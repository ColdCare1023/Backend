"""HU-53: deduplicación persistente del aviso por correo.

`notificada_en` marca el episodio como ya avisado: sobrevive a reinicios del
backend (el cooldown en memoria no) y a la reapertura del episodio dentro de la
ventana de normalización (HU-20), que conserva el mismo id de alerta.
`notificacion_intentos` cuenta los envíos fallidos para acotar los reintentos.

Revision ID: 0027_hu53_notificacion_alerta
Revises: 0026_hu13_conectividad_dispositivo
Create Date: 2026-10-08

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027_hu53_notificacion_alerta"
down_revision: str | None = "0026_hu13_conectividad_dispositivo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("thermal_alerts") as batch:
        batch.add_column(sa.Column("notificada_en", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column("notificacion_intentos", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("thermal_alerts") as batch:
        batch.drop_column("notificacion_intentos")
        batch.drop_column("notificada_en")
