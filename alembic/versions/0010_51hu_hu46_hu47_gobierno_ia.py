"""HU-46/HU-47 (backlog de 51 HU finales, 2026-08-30): registro de gobierno
de versiones del modelo IA y evidencia completa de inferencia por lectura
(vector de features + probabilidad por clase) para auditoría.

No modifica 0001..0009.

Revision ID: 0010_51hu_hu46_hu47_gobierno_ia
Revises: 0009_51hu_hu44_hu10_credenciales
Create Date: 2026-09-02

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from alembic import op

# Mismo criterio que 0001_initial_schema: JSONB en PostgreSQL, JSON plano en
# otros dialectos (usado en pruebas con SQLite).
JSONVariant = JSON().with_variant(JSONB(), "postgresql")

revision: str = "0010_51hu_hu46_hu47_gobierno_ia"
down_revision: str | None = "0009_51hu_hu44_hu10_credenciales"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── HU-46: registro de gobierno de versiones del modelo IA ────────────
    op.create_table(
        "ai_model_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("version", sa.String(50), nullable=False, unique=True),
        sa.Column("model_hash", sa.String(64), nullable=False),
        sa.Column("feature_schema_version", sa.String(20), nullable=False),
        sa.Column("dataset_version", sa.String(50), nullable=False),
        sa.Column("scikit_learn_version", sa.String(20), nullable=False),
        sa.Column("python_version", sa.String(20), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("aprobado_por", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("aprobado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("activa", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("activada_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("desactivada_en", sa.DateTime(timezone=True), nullable=True),
    )

    # ── HU-47: evidencia completa de inferencia por lectura ────────────────
    op.add_column("thermal_readings", sa.Column("probabilidades_ia", JSONVariant, nullable=True))
    op.add_column("thermal_readings", sa.Column("vector_features_ia", JSONVariant, nullable=True))


def downgrade() -> None:
    op.drop_column("thermal_readings", "vector_features_ia")
    op.drop_column("thermal_readings", "probabilidades_ia")
    op.drop_table("ai_model_versions")
