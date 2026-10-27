"""HU-44: prueba de posesión e incidentes de la retención por compromiso.

Confirmar la consola del broker es un paso manual: si el administrador lo
confirma sin haber cambiado la contraseña, el broker sigue aceptando el token
filtrado. La retención ya no termina con la confirmación, sino cuando el nodo
firma un desafío (nonce) con el token de su sesión. El backend guarda una clave
derivada del token (`pop_clave`), nunca el token.

Los incidentes operativos admiten dos tipos nuevos: «monitoreo_suspendido»
(la telemetría está retenida) y «excursion_no_verificada» (una lectura
retenida salió de 2-8 °C y su origen no se puede comprobar). Para ellos
`detalle` guarda la lectura que lo disparó.

Revision ID: 0033_hu44_prueba_posesion
Revises: 0032_hu44_rotacion_compromiso
Create Date: 2026-10-27

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0033_hu44_prueba_posesion"
down_revision: str | None = "0032_hu44_rotacion_compromiso"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("pop_clave", sa.String(64), nullable=True))
        batch.add_column(sa.Column("pop_nonce", sa.String(64), nullable=True))
        batch.add_column(sa.Column("pop_nonce_emitido_en", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("incidentes_operativos") as batch:
        batch.alter_column("tipo", existing_type=sa.String(20), type_=sa.String(40), existing_nullable=False)
        batch.add_column(sa.Column("detalle", JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("incidentes_operativos") as batch:
        batch.drop_column("detalle")
        batch.alter_column("tipo", existing_type=sa.String(40), type_=sa.String(20), existing_nullable=False)
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("pop_nonce_emitido_en")
        batch.drop_column("pop_nonce")
        batch.drop_column("pop_clave")
