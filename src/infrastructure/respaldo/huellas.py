"""HU-29: huella verificable de las tablas de evidencia.

Para comprobar que una restauración devolvió EXACTAMENTE lo respaldado no basta
con contar filas: se calcula, por tabla, el SHA-256 de sus filas en un orden
estable (por clave primaria) con las columnas que constituyen la evidencia.
La misma función corre sobre la base de origen (al respaldar) y sobre la
restaurada (en el entorno aislado); si cualquier valor difiere, la huella
difiere.
"""

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

# Columnas de evidencia por tabla. `traceability_records` incluye el hash y el
# hash previo: si la cadena restaurada no es la misma, aquí se nota.
TABLAS_EVIDENCIA: dict[str, tuple[str, ...]] = {
    "thermal_readings": (
        "id", "device_id", "timestamp", "temperatura_interna", "temperatura_ambiental",
        "humedad_ambiental", "apertura_refrigerador", "reading_id", "boot_id", "seq_no",
        "riesgo_efectivo", "excursion_confirmada", "origen",
    ),
    "traceability_records": ("id", "tipo_evento", "device_id", "chain_id", "previous_hash", "hash_actual"),
    "audit_logs": ("id", "usuario_id", "accion", "recurso", "detalle", "created_at"),
}
LOTE = 5_000


def _normalizar(valor):
    """Representación estable entre PostgreSQL y su copia restaurada (y SQLite
    en pruebas): UUID sin guiones, instantes en UTC sin zona, floats a 6
    decimales, JSON con claves ordenadas."""
    if isinstance(valor, float):
        return round(valor, 6)
    if isinstance(valor, Decimal):
        return str(valor.normalize())
    if isinstance(valor, UUID):
        return valor.hex
    if isinstance(valor, datetime):
        if valor.tzinfo is not None:
            valor = valor.astimezone(timezone.utc).replace(tzinfo=None)
        return valor.isoformat(timespec="microseconds")
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, dict | list):
        return json.loads(json.dumps(valor, sort_keys=True, default=str))
    if isinstance(valor, str) and len(valor) == 36 and valor.count("-") == 4:
        try:
            return UUID(valor).hex
        except ValueError:
            return valor
    return valor


async def huella_tabla(session: AsyncSession, tabla: str, columnas: tuple[str, ...]) -> dict:
    lista = ", ".join(columnas)
    total = (await session.execute(select(func.count()).select_from(text(tabla)))).scalar_one()
    digest = hashlib.sha256()
    desplazamiento = 0
    while desplazamiento < total:
        filas = (
            await session.execute(
                text(f"SELECT {lista} FROM {tabla} ORDER BY id LIMIT :lote OFFSET :desde"),
                {"lote": LOTE, "desde": desplazamiento},
            )
        ).all()
        if not filas:
            break
        for fila in filas:
            digest.update(json.dumps([_normalizar(v) for v in fila], sort_keys=True, default=str).encode())
            digest.update(b"\n")
        desplazamiento += len(filas)
    return {"filas": int(total), "sha256": digest.hexdigest()}


async def huellas_evidencia(session: AsyncSession) -> dict[str, dict]:
    return {tabla: await huella_tabla(session, tabla, cols) for tabla, cols in TABLAS_EVIDENCIA.items()}


def comparar_huellas(origen: dict[str, dict], restaurada: dict[str, dict]) -> dict:
    """Resultado de la prueba de restauración: por tabla, si coinciden filas y
    huella. `coincide` es True solo si coinciden TODAS las tablas."""
    detalle = {}
    for tabla in sorted(set(origen) | set(restaurada)):
        a, b = origen.get(tabla), restaurada.get(tabla)
        detalle[tabla] = {
            "filas_origen": a["filas"] if a else None,
            "filas_restauradas": b["filas"] if b else None,
            "coinciden_filas": bool(a and b and a["filas"] == b["filas"]),
            "coincide_huella": bool(a and b and a["sha256"] == b["sha256"]),
        }
    return {
        "coincide": all(d["coinciden_filas"] and d["coincide_huella"] for d in detalle.values()),
        "tablas": detalle,
    }
