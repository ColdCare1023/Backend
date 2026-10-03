"""deduplicación de lecturas térmicas: restricción única (device_id, timestamp)

Corrige el hallazgo B-04 de la auditoría: un reenvío MQTT (PUBACK perdido,
QoS1) para el mismo dispositivo y el mismo instante exacto no debe producir
un segundo registro en `thermal_readings`.

Revision ID: 0002_thermal_readings_dedup
Revises: 0001_initial_schema
Create Date: 2026-07-22

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_thermal_readings_dedup"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "uq_thermal_readings_device_timestamp"


# `batch_alter_table` en vez de `create_unique_constraint` directo: SQLite no
# soporta ALTER de restricciones, y Alembic necesita su estrategia de
# copiar-y-mover. En PostgreSQL (el motor de despliegue) el resultado es el
# mismo ALTER de siempre; en SQLite permite además verificar la cadena de
# migraciones en las pruebas sin levantar un servidor de base de datos.
def upgrade() -> None:
    with op.batch_alter_table("thermal_readings") as batch:
        batch.create_unique_constraint(CONSTRAINT_NAME, ["device_id", "timestamp"])


def downgrade() -> None:
    with op.batch_alter_table("thermal_readings") as batch:
        batch.drop_constraint(CONSTRAINT_NAME, type_="unique")
