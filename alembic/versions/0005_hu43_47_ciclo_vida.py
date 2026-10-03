"""HU-43..HU-47: ciclo de vida de dispositivos, consentimiento Ley 29733,
anonimización de usuarios, OTA de firmware (simulado, sin ESP32 real) y
recuperación de corrupción de cadena hash.

No modifica 0001..0004.

Revision ID: 0005_hu43_47_ciclo_vida
Revises: 0004_ia_correcciones_p1
Create Date: 2026-07-23

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_hu43_47_ciclo_vida"
down_revision: str | None = "0004_ia_correcciones_p1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # HU-43: ciclo de vida de dispositivos (baja/reemplazo).
    op.add_column("devices", sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("devices", sa.Column("firmware_version", sa.String(20), nullable=False, server_default="1.0.0"))
    op.add_column("devices", sa.Column("motivo_baja", sa.String(50), nullable=True))
    op.add_column("devices", sa.Column("descripcion_baja", sa.Text(), nullable=True))
    op.add_column("devices", sa.Column("dado_de_baja_en", sa.DateTime(timezone=True), nullable=True))
    op.add_column("devices", sa.Column("reemplaza_a_device_id", sa.String(50), nullable=True))

    # HU-44/HU-45: consentimiento de privacidad y ciclo de vida de usuarios.
    op.add_column("users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("users", sa.Column("privacy_accepted", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("privacy_accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("privacy_version_accepted", sa.String(10), nullable=True))
    op.add_column("users", sa.Column("motivo_desactivacion", sa.String(50), nullable=True))
    op.add_column("users", sa.Column("desactivado_en", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("desactivado_por", sa.Uuid(as_uuid=True), nullable=True))
    op.add_column("users", sa.Column("anonymized_for_gdpr", sa.Boolean(), nullable=False, server_default=sa.false()))
    # batch_alter_table: SQLite no soporta ALTER de restricciones. En
    # PostgreSQL emite el mismo ALTER TABLE ... ADD CONSTRAINT.
    with op.batch_alter_table("users") as batch:
        batch.create_foreign_key(
            "fk_users_desactivado_por", "users", ["desactivado_por"], ["id"]
        )

    # HU-46: OTA de firmware (simulado a nivel de metadata; no hay firmware ni
    # ESP32 real en este repositorio — ver 08 del informe de cierre).
    op.create_table(
        "firmware_releases",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("version", sa.String(20), nullable=False, unique=True),
        sa.Column("hash_sha256", sa.String(64), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=False),
        sa.Column("fecha_compilacion", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "firmware_deployments",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("device_id", sa.String(50), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("version_objetivo", sa.String(20), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("programado_para", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resultado", sa.Text(), nullable=True),
        sa.Column("completado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # HU-47: recuperación de corrupción de cadena hash.
    op.add_column(
        "traceability_records", sa.Column("is_corrupted", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column(
        "traceability_records",
        sa.Column("is_after_corruption", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "forensic_snapshots",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("registro_id", sa.Uuid(as_uuid=True), sa.ForeignKey("traceability_records.id"), nullable=True),
        sa.Column("detalle", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "system_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cadena_comprometida", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("INSERT INTO system_state (id, cadena_comprometida) VALUES (1, false)")


def downgrade() -> None:
    op.drop_table("system_state")
    op.drop_table("forensic_snapshots")
    op.drop_column("traceability_records", "is_after_corruption")
    op.drop_column("traceability_records", "is_corrupted")
    op.drop_table("firmware_deployments")
    op.drop_table("firmware_releases")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("fk_users_desactivado_por", type_="foreignkey")
    op.drop_column("users", "anonymized_for_gdpr")
    op.drop_column("users", "desactivado_por")
    op.drop_column("users", "desactivado_en")
    op.drop_column("users", "motivo_desactivacion")
    op.drop_column("users", "privacy_version_accepted")
    op.drop_column("users", "privacy_accepted_at")
    op.drop_column("users", "privacy_accepted")
    op.drop_column("users", "is_active")
    op.drop_column("devices", "reemplaza_a_device_id")
    op.drop_column("devices", "dado_de_baja_en")
    op.drop_column("devices", "descripcion_baja")
    op.drop_column("devices", "motivo_baja")
    op.drop_column("devices", "firmware_version")
    op.drop_column("devices", "activo")
