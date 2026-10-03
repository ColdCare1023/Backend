"""esquema inicial: devices, roles, users, thermal_readings, thermal_alerts,
corrective_actions, traceability_records, audit_logs, report_exports

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-07-03

"""
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONVariant = JSON().with_variant(JSONB(), "postgresql")

ROLES_INICIALES = ("administrador", "farmaceutico", "tecnico")


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("nombre", sa.String(120), nullable=True),
        sa.Column("ubicacion", sa.String(200), nullable=True),
        sa.Column("estado_conectividad", sa.String(20), nullable=False, server_default="offline"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "roles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("nombre", sa.String(30), nullable=False, unique=True),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("nombre", sa.String(120), nullable=False),
        sa.Column("email", sa.String(150), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("rol_id", sa.Uuid(), sa.ForeignKey("roles.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "thermal_readings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device_id", sa.String(50), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("temperatura_ambiental", sa.Float(), nullable=True),
        sa.Column("humedad_ambiental", sa.Float(), nullable=True),
        sa.Column("temperatura_interna", sa.Float(), nullable=True),
        sa.Column("apertura_refrigerador", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("nivel_riesgo", sa.String(30), nullable=True),
        sa.Column("estado_conectividad", sa.String(20), nullable=True),
        sa.Column("payload", JSONVariant, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_thermal_readings_device_id", "thermal_readings", ["device_id"])
    op.create_index("ix_thermal_readings_timestamp", "thermal_readings", ["timestamp"])

    op.create_table(
        "thermal_alerts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("reading_id", sa.Uuid(), sa.ForeignKey("thermal_readings.id"), nullable=False),
        sa.Column("device_id", sa.String(50), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("nivel_riesgo", sa.String(30), nullable=False),
        sa.Column("mensaje", sa.Text(), nullable=False),
        sa.Column("revisada", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revisada_por", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_thermal_alerts_device_id", "thermal_alerts", ["device_id"])

    op.create_table(
        "corrective_actions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("alert_id", sa.Uuid(), sa.ForeignKey("thermal_alerts.id"), nullable=False),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "traceability_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tipo_evento", sa.String(50), nullable=False),
        sa.Column("device_id", sa.String(50), nullable=True),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("payload", JSONVariant, nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_hash", sa.String(64), nullable=False),
        sa.Column("hash_actual", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("accion", sa.String(100), nullable=False),
        sa.Column("recurso", sa.String(100), nullable=False),
        sa.Column("detalle", JSONVariant, nullable=True),
        sa.Column("ip_origen", sa.String(45), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "report_exports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("tipo_reporte", sa.String(50), nullable=False),
        sa.Column("fecha_desde", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fecha_hasta", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archivo_url", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    roles_table = sa.table("roles", sa.column("id", sa.Uuid()), sa.column("nombre", sa.String()))
    op.bulk_insert(roles_table, [{"id": uuid.uuid4(), "nombre": nombre} for nombre in ROLES_INICIALES])


def downgrade() -> None:
    op.drop_table("report_exports")
    op.drop_table("audit_logs")
    op.drop_table("traceability_records")
    op.drop_table("corrective_actions")
    op.drop_table("thermal_alerts")
    op.drop_table("thermal_readings")
    op.drop_table("users")
    op.drop_table("roles")
    op.drop_table("devices")
