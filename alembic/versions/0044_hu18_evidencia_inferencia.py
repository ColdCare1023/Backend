"""HU-18 esc. 1 y 4: evidencia completa de cada inferencia preventiva.

`predicciones_preventivas` guardaba como `timestamp_inferencia` el instante de
la lectura evaluada (el instante t de las features), no cuándo corrió el
modelo, y no registraba la versión del esquema de características. Se añaden:

- `inferido_en`: hora UTC en que se ejecutó la inferencia.
- `conjunto_features`: versión del esquema de características del manifiesto
  (p. ej. «termico_puerta»), vigente al inferir.

Columnas nulas: las filas anteriores conservan su valor original (la tabla es
de solo anexado, migración 0025) y `ADD COLUMN` no dispara sus triggers.
"""

import sqlalchemy as sa

from alembic import op

revision = "0044_hu18_evidencia_inferencia"
down_revision = "0043_intervalo_inicial_10s"
branch_labels = None
depends_on = None

_TABLA = "predicciones_preventivas"


# Sin `batch_alter_table`: en SQLite recrearía la tabla y perdería los triggers
# de solo anexado (0025). ADD/DROP COLUMN nativos los conservan.
def upgrade() -> None:
    op.add_column(_TABLA, sa.Column("inferido_en", sa.DateTime(timezone=True), nullable=True))
    op.add_column(_TABLA, sa.Column("conjunto_features", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLA, "conjunto_features")
    op.drop_column(_TABLA, "inferido_en")
