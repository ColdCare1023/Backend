"""HU-28: acciones correctivas append-only y motivo de rectificación.

- `motivo_rectificacion`: por qué se corrigió una acción (P6). NULL en las
  acciones originales.
- En PostgreSQL, un trigger rechaza UPDATE y DELETE sobre `corrective_actions`:
  «la rectificación conserva el original» deja de depender de que la
  aplicación se porte bien y pasa a garantizarlo la base de datos, incluso
  ante un UPDATE manual. En SQLite (pruebas) no se crea.

Revision ID: 0028_hu28_acciones_append_only
Revises: 0027_hu53_notificacion_alerta
Create Date: 2026-10-08

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028_hu28_acciones_append_only"
down_revision: str | None = "0027_hu53_notificacion_alerta"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCION = """
CREATE OR REPLACE FUNCTION corrective_actions_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'corrective_actions es append-only (HU-28): % no permitido', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER = """
CREATE TRIGGER trg_corrective_actions_append_only
BEFORE UPDATE OR DELETE ON corrective_actions
FOR EACH ROW EXECUTE FUNCTION corrective_actions_append_only();
"""


def upgrade() -> None:
    with op.batch_alter_table("corrective_actions") as batch:
        batch.add_column(sa.Column("motivo_rectificacion", sa.Text(), nullable=True))
    if op.get_bind().dialect.name == "postgresql":
        op.execute(_FUNCION)
        op.execute(_TRIGGER)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_corrective_actions_append_only ON corrective_actions")
        op.execute("DROP FUNCTION IF EXISTS corrective_actions_append_only()")
    with op.batch_alter_table("corrective_actions") as batch:
        batch.drop_column("motivo_rectificacion")
