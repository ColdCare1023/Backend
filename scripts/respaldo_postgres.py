"""HU-29: respaldo programado de PostgreSQL con alerta crítica si falla.

Uso (servicio cron de Railway o cron del host, p. ej. cada día a las 02:00):

    DATABASE_URL=postgresql://... python -m scripts.respaldo_postgres --destino /respaldos

1. `pg_dump --format=custom` de la base de producción a `<destino>/coldcare_<UTC>.dump`.
2. SHA-256 del archivo y huella (filas + SHA-256 por tabla) de
   `thermal_readings`, `traceability_records` y `audit_logs`, en
   `<destino>/coldcare_<UTC>.manifiesto.json`. El manifiesto es lo que la
   prueba de restauración (`scripts.verificar_restauracion`) compara.
3. Deja RESPALDO_COMPLETADO en `audit_logs`.

Escenario 3: si cualquier paso falla, escribe un log de nivel CRITICAL
(«RESPALDO FALLIDO»), intenta dejar RESPALDO_FALLIDO en `audit_logs` y sale con
código 2, para que la plataforma marque la ejecución como fallida y avise.

El respaldo automático de Railway (si el plan lo incluye) no reemplaza a este:
no deja huella verificable ni se puede restaurar en un entorno aislado sin
tocar producción.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.infrastructure.config import get_settings
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.infrastructure.database.session import build_engine, build_session_factory
from src.infrastructure.respaldo.huellas import huellas_evidencia

logger = logging.getLogger("respaldo")
CODIGO_FALLO = 2


def url_libpq(url: str) -> str:
    """pg_dump/pg_restore entienden `postgresql://`, no el dialecto asyncpg."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1).replace("ssl=", "sslmode=")


def sha256_archivo(ruta: Path) -> str:
    digest = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1 << 20), b""):
            digest.update(bloque)
    return digest.hexdigest()


async def _huellas(database_url: str) -> dict:
    engine = build_engine(database_url)
    try:
        async with build_session_factory(engine)() as session:
            return await huellas_evidencia(session)
    finally:
        await engine.dispose()


async def _auditar(database_url: str, accion: str, detalle: dict) -> None:
    engine = build_engine(database_url)
    try:
        async with build_session_factory(engine)() as session:
            await SQLAlchemyAuditLogRepository(session).registrar(
                usuario_id=None, accion=accion, recurso="respaldo/postgres", detalle=detalle
            )
            await session.commit()
    finally:
        await engine.dispose()


def ejecutar_pg_dump(database_url: str, archivo: Path) -> None:
    if shutil.which("pg_dump") is None:
        raise RuntimeError("pg_dump no está instalado en esta imagen")
    subprocess.run(
        ["pg_dump", "--format=custom", "--no-owner", "--no-privileges", f"--file={archivo}", url_libpq(database_url)],
        check=True, capture_output=True, text=True, timeout=3600,
    )


async def respaldar(database_url: str, destino: Path) -> dict:
    destino.mkdir(parents=True, exist_ok=True)
    marca = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archivo = destino / f"coldcare_{marca}.dump"
    # La huella se toma antes del volcado: una lectura que entre durante el
    # pg_dump queda en el archivo pero no en la huella, y la verificación lo
    # señalaría. Por eso se programa en la ventana de menos tráfico y se
    # compara contra el manifiesto, no contra la base viva.
    ejecutar_pg_dump(database_url, archivo)
    manifiesto = {
        "archivo": archivo.name,
        "sha256": sha256_archivo(archivo),
        "bytes": archivo.stat().st_size,
        "generado_en": datetime.now(tz=timezone.utc).isoformat(),
        "huellas": await _huellas(database_url),
    }
    (destino / f"coldcare_{marca}.manifiesto.json").write_text(json.dumps(manifiesto, indent=2), encoding="utf-8")
    return manifiesto


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(prog="respaldo_postgres")
    parser.add_argument("--destino", type=Path, default=Path(os.environ.get("RESPALDO_DESTINO", "respaldos")))
    args = parser.parse_args(argv)
    database_url = get_settings().database_url
    try:
        manifiesto = asyncio.run(respaldar(database_url, args.destino))
    except Exception as exc:
        motivo = exc.stderr.strip()[:300] if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)[:300]
        logger.critical("RESPALDO FALLIDO: %s", motivo)
        try:
            asyncio.run(_auditar(database_url, "RESPALDO_FALLIDO", {"error": motivo}))
        except Exception:
            logger.critical("RESPALDO FALLIDO y no se pudo registrar en audit_logs.")
        return CODIGO_FALLO
    asyncio.run(_auditar(database_url, "RESPALDO_COMPLETADO", {
        "archivo": manifiesto["archivo"], "sha256": manifiesto["sha256"],
        "filas": {t: h["filas"] for t, h in manifiesto["huellas"].items()},
    }))
    logger.info("Respaldo completado: %s (%d bytes)", manifiesto["archivo"], manifiesto["bytes"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
