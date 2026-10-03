"""HU-37 (checklist BPA persistente), HU-30 (calibración de sensores con
trazabilidad) e índices de consulta para las vistas de historial.

No modifica 0001..0005.

Revision ID: 0006_checklist_calibracion_indices
Revises: 0005_hu43_47_ciclo_vida
Create Date: 2026-07-25

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_checklist_calibracion_indices"
down_revision: str | None = "0005_hu43_47_ciclo_vida"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── HU-37: checklist BPA persistente ──────────────────────────────────
    op.create_table(
        "checklist_bpa",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("fecha", sa.String(10), nullable=False),
        sa.Column("temperatura", sa.Boolean(), nullable=False),
        sa.Column("termometro", sa.Boolean(), nullable=False),
        sa.Column("registros", sa.Boolean(), nullable=False),
        sa.Column("alertas_revisadas", sa.Boolean(), nullable=False),
        sa.Column("acciones_documentadas", sa.Boolean(), nullable=False),
        sa.Column("puerta", sa.Boolean(), nullable=False),
        sa.Column("limpieza", sa.Boolean(), nullable=False),
        sa.Column("exclusivo", sa.Boolean(), nullable=False),
        sa.Column("rotulado", sa.Boolean(), nullable=False),
        sa.Column("respaldo", sa.Boolean(), nullable=False),
        sa.Column("observaciones", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("usuario_id", "fecha", name="uq_checklist_bpa_usuario_fecha"),
    )
    op.create_index("ix_checklist_bpa_usuario_id", "checklist_bpa", ["usuario_id"])

    # ── HU-30: calibración de sensores ────────────────────────────────────
    op.add_column("devices", sa.Column("fecha_ultima_calibracion", sa.Date(), nullable=True))
    op.add_column("devices", sa.Column("numero_certificado_calibracion", sa.String(100), nullable=True))
    op.add_column("devices", sa.Column("fecha_proxima_calibracion", sa.Date(), nullable=True))
    op.add_column("devices", sa.Column("observaciones_calibracion", sa.Text(), nullable=True))

    # ── B-11: índices de las consultas de historial ───────────────────────
    # Todas las vistas (dashboard, historial, reportes, auditoría) ordenan por
    # tiempo descendente y filtran por dispositivo; sin estos índices el plan
    # degrada a scan secuencial conforme crece la serie temporal.
    op.create_index(
        "idx_thermal_readings_device_ts",
        "thermal_readings",
        ["device_id", sa.text("timestamp DESC")],
    )
    op.create_index(
        "idx_thermal_alerts_device_created",
        "thermal_alerts",
        ["device_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_traceability_records_created",
        "traceability_records",
        [sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_audit_logs_created",
        "audit_logs",
        [sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_audit_logs_created", table_name="audit_logs")
    op.drop_index("idx_traceability_records_created", table_name="traceability_records")
    op.drop_index("idx_thermal_alerts_device_created", table_name="thermal_alerts")
    op.drop_index("idx_thermal_readings_device_ts", table_name="thermal_readings")

    op.drop_column("devices", "observaciones_calibracion")
    op.drop_column("devices", "fecha_proxima_calibracion")
    op.drop_column("devices", "numero_certificado_calibracion")
    op.drop_column("devices", "fecha_ultima_calibracion")

    op.drop_index("ix_checklist_bpa_usuario_id", table_name="checklist_bpa")
    op.drop_table("checklist_bpa")
