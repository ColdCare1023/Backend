"""HU-22 / HU-11: `thermal_readings.boot_id` y `seq_no` pasan a BIGINT.

El contrato v1.0 admite `boot_id` y `seq_no` hasta UINT32 (4 294 967 295,
`contrato_v1.UINT32`), pero las columnas eran INTEGER de PostgreSQL (máximo
2 147 483 647). Un valor entre ambos límites pasaba la validación y fallaba en
el INSERT: sin COMMIT no hay acuse, el nodo reintenta para siempre y su cola
FIFO de LittleFS queda bloqueada (HU-07/HU-22 esc. 3). Las demás columnas de
identidad del nodo (`devices.conectividad_boot_id`, `estado_nodo_dispositivo`,
`episodios_buffer_saturado`) ya eran BIGINT.

PostgreSQL: `ALTER COLUMN ... TYPE BIGINT` reescribe la tabla y sus índices
(incluido el único parcial de la identidad) bajo un bloqueo exclusivo breve; no
dispara los triggers de solo anexado (0018) ni cambia ningún valor.

SQLite (desarrollo): una columna INTEGER ya guarda 64 bits; se recrea la tabla
en modo batch para que el esquema declare BIGINT y se vuelven a crear los
triggers de solo anexado de 0018, que SQLite borra junto con la tabla.

Downgrade: se niega si alguna fila ya no cabe en INTEGER (no se trunca evidencia).

Revision ID: 0042_bigint_identidad
Revises: 0041_hu20_normalizacion_preventiva
Create Date: 2026-11-14

"""
import warnings
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0042_bigint_identidad"
down_revision: str | None = "0041_hu20_normalizacion_preventiva"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNAS = ("boot_id", "seq_no")
INT4_MAX = 2_147_483_647

# Copia literal de 0018_evidencia_solo_anexado (SQLite): la recreación batch de
# la tabla elimina sus triggers.
_COLUMNAS_FIJAS_LECTURAS = (
    "id, device_id, timestamp, temperatura_ambiental, humedad_ambiental, temperatura_interna, "
    "apertura_refrigerador, nivel_riesgo, estado_conectividad, payload, created_at, modelo_version, "
    "confianza_ia, origen_clasificacion, estado_inferencia, motivo_no_inferencia, reading_id, "
    "schema_version, excursion_confirmada, riesgo_efectivo, probabilidades_ia, vector_features_ia, "
    "boot_id, seq_no, time_quality, received_at, uptime_ms, origen, registrado_por"
)
_RECHAZO_SQLITE = "SELECT RAISE(ABORT, 'tabla de evidencia de solo anexado')"
_TRIGGERS_SQLITE_LECTURAS = {
    "thermal_readings_sin_borrado": "BEFORE DELETE ON thermal_readings",
    "thermal_readings_solo_reanclaje": f"BEFORE UPDATE OF {_COLUMNAS_FIJAS_LECTURAS} ON thermal_readings",
}


def _dialecto() -> str:
    return op.get_bind().dialect.name


def _cambiar_tipo(nuevo: sa.types.TypeEngine, anterior: sa.types.TypeEngine) -> None:
    if _dialecto() == "sqlite":
        with warnings.catch_warnings():
            # La reflexión de la tabla alcanza `users` por su FK y avisa de que no
            # refleja el índice por expresión `ix_users_email_lower`; `users` no
            # se toca y su índice sigue en su sitio.
            warnings.filterwarnings("ignore", message="Skipped unsupported reflection of expression-based index")
            with op.batch_alter_table("thermal_readings", recreate="always") as batch:
                for columna in COLUMNAS:
                    batch.alter_column(columna, type_=nuevo, existing_type=anterior, existing_nullable=True)
        for nombre, evento in _TRIGGERS_SQLITE_LECTURAS.items():
            op.execute(f"DROP TRIGGER IF EXISTS {nombre}")
            op.execute(f"CREATE TRIGGER {nombre} {evento} BEGIN {_RECHAZO_SQLITE}; END")
        return
    for columna in COLUMNAS:
        op.alter_column("thermal_readings", columna, type_=nuevo, existing_type=anterior, existing_nullable=True)


def upgrade() -> None:
    _cambiar_tipo(sa.BigInteger(), sa.Integer())


def downgrade() -> None:
    fuera_de_rango = op.get_bind().execute(
        sa.text(
            "SELECT COUNT(*) FROM thermal_readings WHERE boot_id > :maximo OR seq_no > :maximo"
        ),
        {"maximo": INT4_MAX},
    ).scalar()
    if fuera_de_rango:
        raise RuntimeError(
            f"{fuera_de_rango} lecturas tienen boot_id o seq_no mayores que {INT4_MAX}: volver a INTEGER "
            "las truncaría. Downgrade cancelado (thermal_readings es evidencia de solo anexado)."
        )
    _cambiar_tipo(sa.Integer(), sa.BigInteger())
