"""HU-54: incidente SIN_DATOS persistente y umbral N x intervalo del nodo.

Hasta ahora SIN_DATOS solo se calculaba al consultar `/api/dispositivos/
estado-operativo`: si nadie abría el tablero, el silencio del nodo no dejaba
rastro ni avisaba a nadie. La tarea periódica del backend abre aquí un
incidente cuando el nodo calla más de N x su intervalo de captura, lo
notifica tras el COMMIT y lo cierra al volver las lecturas.

- `devices.intervalo_reporte_segundos`: intervalo de captura del nodo (30 s
  de fábrica). Lo informa el propio nodo en `estado_nodo` o lo fija el
  administrador; el umbral SIN_DATOS se recalcula con él.
- `devices.ultimo_latido_en`: último `estado_nodo` recibido. Distingue un nodo
  vivo que no entrega lecturas de uno apagado o sin red.
- `incidentes_operativos`: un registro por episodio de silencio. El índice
  único parcial garantiza un solo incidente abierto por dispositivo y tipo.

Revision ID: 0030_hu54_incidentes_sin_datos
Revises: 0029_hu43_hu44_pendiente_broker
Create Date: 2026-10-26

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030_hu54_incidentes_sin_datos"
down_revision: str | None = "0029_hu43_hu44_pendiente_broker"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(
            sa.Column("intervalo_reporte_segundos", sa.Integer(), nullable=False, server_default="30")
        )
        batch.add_column(sa.Column("ultimo_latido_en", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "incidentes_operativos",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device_id", sa.String(50), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("abierto_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ultimo_reporte_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("umbral_segundos", sa.Integer(), nullable=False),
        sa.Column("resuelto_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notificado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notificacion_intentos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_incidentes_operativos_device_id", "incidentes_operativos", ["device_id"])
    op.create_index(
        "uq_incidentes_operativos_abierto",
        "incidentes_operativos",
        ["device_id", "tipo"],
        unique=True,
        postgresql_where=sa.text("resuelto_en IS NULL"),
        sqlite_where=sa.text("resuelto_en IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_incidentes_operativos_abierto", table_name="incidentes_operativos")
    op.drop_index("ix_incidentes_operativos_device_id", table_name="incidentes_operativos")
    op.drop_table("incidentes_operativos")
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("ultimo_latido_en")
        batch.drop_column("intervalo_reporte_segundos")
