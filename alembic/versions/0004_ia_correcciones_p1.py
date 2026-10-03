"""Fase de corrección P1 (AIV-02/AIV-03/AIV-07): control de episodio de
alerta (elimina la tormenta de alertas) y estado real de la inferencia por
lectura (elimina la ambigüedad de confianza_ia=0.0).

No modifica 0001, 0002 ni 0003.

Revision ID: 0004_ia_correcciones_p1
Revises: 0003_lecturas_modelo_ia
Create Date: 2026-07-22

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_ia_correcciones_p1"
down_revision: str | None = "0003_lecturas_modelo_ia"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # AIV-07: estado real de la inferencia (completada/omitida/fallida/
    # modelo_no_disponible) y motivo breve — NULL en registros previos a esta
    # migración, tratados por el backend como "sin dato de esta corrección".
    op.add_column("thermal_readings", sa.Column("estado_inferencia", sa.String(30), nullable=True))
    op.add_column("thermal_readings", sa.Column("motivo_no_inferencia", sa.String(100), nullable=True))

    # AIV-02: control de episodio de alerta.
    op.add_column(
        "thermal_alerts", sa.Column("episodio_abierto", sa.Integer(), nullable=True)
    )
    op.add_column(
        "thermal_alerts",
        sa.Column("lectura_inicial_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.add_column(
        "thermal_alerts",
        sa.Column("lectura_mas_reciente_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.add_column(
        "thermal_alerts",
        sa.Column("ultima_actualizacion", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "thermal_alerts", sa.Column("cerrada_en", sa.DateTime(timezone=True), nullable=True)
    )

    # Backfill de filas existentes: se tratan como episodios ya cerrados
    # (episodio_abierto queda NULL) para no violar la nueva restricción única
    # sobre filas que en el modelo anterior no tenían noción de "episodio".
    op.execute(
        "UPDATE thermal_alerts SET "
        "lectura_inicial_id = reading_id, "
        "lectura_mas_reciente_id = reading_id, "
        "ultima_actualizacion = created_at, "
        "cerrada_en = created_at "
        "WHERE lectura_inicial_id IS NULL"
    )

    # batch_alter_table: SQLite no soporta ALTER de columnas ni de
    # restricciones; Alembic aplica su estrategia de copiar-y-mover. En
    # PostgreSQL (motor de despliegue) emite exactamente los mismos ALTER.
    with op.batch_alter_table("thermal_alerts") as batch:
        batch.alter_column("lectura_inicial_id", nullable=False)
        batch.alter_column("lectura_mas_reciente_id", nullable=False)
        batch.alter_column("ultima_actualizacion", nullable=False)
        batch.create_foreign_key(
            "fk_thermal_alerts_lectura_inicial_id",
            "thermal_readings",
            ["lectura_inicial_id"], ["id"],
        )
        batch.create_foreign_key(
            "fk_thermal_alerts_lectura_mas_reciente_id",
            "thermal_readings",
            ["lectura_mas_reciente_id"], ["id"],
        )
        batch.create_unique_constraint(
            "uq_thermal_alerts_episodio_abierto_por_device_y_riesgo",
            ["device_id", "nivel_riesgo", "episodio_abierto"],
        )


def downgrade() -> None:
    with op.batch_alter_table("thermal_alerts") as batch:
        batch.drop_constraint(
            "uq_thermal_alerts_episodio_abierto_por_device_y_riesgo", type_="unique"
        )
        batch.drop_constraint("fk_thermal_alerts_lectura_mas_reciente_id", type_="foreignkey")
        batch.drop_constraint("fk_thermal_alerts_lectura_inicial_id", type_="foreignkey")
    op.drop_column("thermal_alerts", "cerrada_en")
    op.drop_column("thermal_alerts", "ultima_actualizacion")
    op.drop_column("thermal_alerts", "lectura_mas_reciente_id")
    op.drop_column("thermal_alerts", "lectura_inicial_id")
    op.drop_column("thermal_alerts", "episodio_abierto")
    op.drop_column("thermal_readings", "motivo_no_inferencia")
    op.drop_column("thermal_readings", "estado_inferencia")
