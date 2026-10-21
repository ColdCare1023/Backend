"""BE-014: el consentimiento de privacidad no se presume.

`users.privacy_accepted` nacía en true: ningún usuario nuevo llegaba a ver la
política de la Ley N.° 29733. Desde aquí nace en false; el acceso exige además
la versión vigente aceptada, así que los usuarios existentes sin versión
registrada se lo volverán a confirmar en su próximo acceso.

Revision ID: 0020_consentimiento_no_presunto
Revises: 0019_jti_invalidados
Create Date: 2026-10-21

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020_consentimiento_no_presunto"
down_revision: str | None = "0019_jti_invalidados"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column("privacy_accepted", existing_type=sa.Boolean(), server_default=sa.false())


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column("privacy_accepted", existing_type=sa.Boolean(), server_default=None)
