"""HU-29: respaldo programado de PostgreSQL con alerta crítica si falla.

Uso (servicio cron de Railway, docs/operacion/RESPALDO_RAILWAY.md):

    DATABASE_URL=postgresql://... python -m scripts.respaldo_postgres --destino /respaldos

1. Abre una transacción REPEATABLE READ de solo lectura y exporta su snapshot
   (`pg_export_snapshot()`).
2. `pg_dump --format=custom --snapshot=<id>` a `<destino>/coldcare_<UTC>.dump.parcial`:
   el volcado ve exactamente ese snapshot.
3. Dentro de la MISMA transacción calcula la huella (filas + SHA-256 por
   tabla) de las tablas de evidencia (`TABLAS_EVIDENCIA`): thermal_readings,
   traceability_records (con el contenido de cada eslabón), audit_logs, users,
   devices, thermal_alerts, corrective_actions, device_config_history y
   ai_model_versions. Volcado y huella describen el
   mismo instante aunque el nodo siga publicando: una lectura que entre
   durante o después del pg_dump no está en ninguno de los dos.
4. Escribe el manifiesto (SHA-256 del archivo, huellas y columnas), que es lo
   que compara la prueba de restauración (`scripts.verificar_restauracion`).
   Solo entonces el volcado y el manifiesto toman su nombre final,
   `coldcare_<UTC>.dump` y `coldcare_<UTC>.manifiesto.json`, y queda
   RESPALDO_COMPLETADO en `audit_logs`. Un proceso que muere a mitad (incluso
   por SIGKILL) solo deja `.parcial`, que la retención no cuenta. Si ya hay un
   respaldo con la misma marca (mismo segundo), no se vuelve a volcar encima.

Escenario 3: si cualquier paso falla, escribe un log de nivel CRITICAL
(«RESPALDO FALLIDO»), borra los `.parcial` que esa ejecución dejara a medias
(nunca un respaldo terminado), intenta dejar RESPALDO_FALLIDO en `audit_logs`
y sale con código 2, para que la plataforma marque la ejecución como fallida
y avise.

El respaldo de volumen de Railway (si el plan lo incluye) no reemplaza a este:
no deja huella verificable ni se puede restaurar en un entorno aislado sin
tocar producción.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from src.infrastructure.config import get_settings
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.infrastructure.database.session import build_engine, build_session_factory
from src.infrastructure.respaldo.huellas import TABLAS_EVIDENCIA, huellas_evidencia

logger = logging.getLogger("respaldo")
CODIGO_FALLO = 2
# Sufijo de lo que una ejecución escribe mientras no terminó: no lleva el
# nombre de un respaldo hasta que el volcado y su huella están completos.
SUFIJO_PARCIAL = ".parcial"


def url_libpq(url: str) -> str:
    """pg_dump/pg_restore entienden `postgresql://`, no el dialecto asyncpg."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1).replace("ssl=", "sslmode=")


def sha256_archivo(ruta: Path) -> str:
    digest = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1 << 20), b""):
            digest.update(bloque)
    return digest.hexdigest()


async def _conexion_snapshot(conexion: AsyncConnection) -> AsyncConnection:
    """Transacción REPEATABLE READ de solo lectura: todas sus consultas ven
    el snapshot tomado en la primera."""
    if conexion.dialect.name != "postgresql":
        return conexion
    return await conexion.execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True)


async def _huellas(database_url: str, columnas: dict | None = None) -> dict:
    """Huella de una base (la restaurada, en la verificación), leída en un
    único snapshot."""
    engine = build_engine(database_url)
    try:
        async with engine.connect() as conexion:
            conexion = await _conexion_snapshot(conexion)
            async with conexion.begin():
                return await huellas_evidencia(conexion, columnas)
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


def _major(version: str) -> int:
    coincidencia = re.search(r"(\d+)(?:\.\d+)?", version)
    if coincidencia is None:
        raise RuntimeError(f"Versión de PostgreSQL no reconocida: {version!r}")
    return int(coincidencia.group(1))


def version_pg_dump() -> str:
    if shutil.which("pg_dump") is None:
        raise RuntimeError("pg_dump no está instalado en esta imagen")
    salida = subprocess.run(["pg_dump", "--version"], check=True, capture_output=True, text=True, timeout=30)
    return salida.stdout.strip()


def verificar_cliente_compatible(version_servidor_num: int) -> None:
    """pg_dump no puede volcar un servidor de una versión mayor más nueva:
    se falla con un motivo claro (y RESPALDO_FALLIDO) en lugar del error de
    pg_dump. Dockerfile.respaldo instala el cliente de la versión indicada."""
    cliente = _major(version_pg_dump())
    servidor = version_servidor_num // 10000
    if cliente < servidor:
        raise RuntimeError(
            f"pg_dump {cliente} es anterior al servidor PostgreSQL {servidor}: construya Dockerfile.respaldo "
            f"con PG_MAJOR={servidor}"
        )


def ejecutar_pg_dump(database_url: str, archivo: Path, snapshot: str | None = None) -> None:
    if shutil.which("pg_dump") is None:
        raise RuntimeError("pg_dump no está instalado en esta imagen")
    comando = ["pg_dump", "--format=custom", "--no-owner", "--no-privileges", f"--file={archivo}"]
    if snapshot:
        comando.append(f"--snapshot={snapshot}")
    subprocess.run(
        [*comando, url_libpq(database_url)], check=True, capture_output=True, text=True, timeout=3600,
    )


def marca_utc() -> str:
    """Marca de los archivos de un respaldo (`coldcare_<marca>.*`): la hora UTC,
    que también ordena la retención. Llega al segundo: dos ejecuciones en el
    mismo segundo comparten marca."""
    return datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def archivos_respaldo(destino: Path, marca: str) -> tuple[Path, Path]:
    """Nombres finales del volcado y del manifiesto de una marca."""
    return destino / f"coldcare_{marca}.dump", destino / f"coldcare_{marca}.manifiesto.json"


def ruta_parcial(ruta: Path) -> Path:
    return ruta.with_name(ruta.name + SUFIJO_PARCIAL)


def _no_pisar(*rutas: Path) -> None:
    """Un respaldo terminado con la misma marca no se vuelve a volcar encima:
    su RESPALDO_COMPLETADO quedaría apuntando a un SHA-256 que ya no existe."""
    for ruta in rutas:
        if ruta.exists():
            raise FileExistsError(
                f"{ruta.name} ya existe: otro respaldo usó la misma marca (mismo segundo) y no se sobrescribe"
            )


async def respaldar(database_url: str, destino: Path, marca: str | None = None) -> dict:
    if shutil.which("pg_dump") is None:
        raise RuntimeError("pg_dump no está instalado en esta imagen")
    destino.mkdir(parents=True, exist_ok=True)
    marca = marca or marca_utc()
    archivo, ruta_manifiesto = archivos_respaldo(destino, marca)
    volcado_parcial, manifiesto_parcial = ruta_parcial(archivo), ruta_parcial(ruta_manifiesto)
    engine = build_engine(database_url)
    try:
        async with engine.connect() as conexion:
            conexion = await _conexion_snapshot(conexion)
            async with conexion.begin():
                # Primera sentencia de la transacción: fija y exporta su snapshot.
                snapshot = (await conexion.execute(text("SELECT pg_export_snapshot()"))).scalar_one()
                version_servidor = int((await conexion.execute(text("SHOW server_version_num"))).scalar_one())
                verificar_cliente_compatible(version_servidor)
                _no_pisar(archivo, ruta_manifiesto)
                # pg_dump importa el snapshot mientras esta transacción sigue
                # abierta; luego la huella se calcula sobre el MISMO snapshot.
                await asyncio.to_thread(ejecutar_pg_dump, database_url, volcado_parcial, snapshot)
                huellas = await huellas_evidencia(conexion)
    finally:
        await engine.dispose()
    manifiesto = {
        "archivo": archivo.name,
        "sha256": sha256_archivo(volcado_parcial),
        "bytes": volcado_parcial.stat().st_size,
        "generado_en": datetime.now(tz=timezone.utc).isoformat(),
        # Volcado y huella salen del mismo snapshot exportado.
        "consistencia": "snapshot_exportado_repeatable_read",
        "postgres_servidor": version_servidor,
        "huellas": huellas,
        # La verificación recalcula la huella con estas mismas columnas.
        "columnas": {tabla: list(cols) for tabla, cols in TABLAS_EVIDENCIA.items()},
    }
    manifiesto_parcial.write_text(json.dumps(manifiesto, indent=2), encoding="utf-8")
    # Primero el volcado: si algo cortara entre ambos, queda un .dump completo
    # (restaurable), nunca un manifiesto que apunte a un archivo inexistente.
    _no_pisar(archivo, ruta_manifiesto)
    os.replace(volcado_parcial, archivo)
    os.replace(manifiesto_parcial, ruta_manifiesto)
    return manifiesto


def podar_respaldos(destino: Path, retener: int) -> list[str]:
    """Retención del volumen: conserva los `retener` respaldos más recientes
    (archivo .dump, su manifiesto y su informe de restauración) y borra los
    anteriores. `retener <= 0` no borra nada (comportamiento por omisión).
    Un `.dump.parcial` no es un respaldo: ni cuenta ni se borra aquí."""
    if retener <= 0:
        return []
    volcados = sorted(destino.glob("coldcare_*.dump"), reverse=True)  # el nombre lleva la hora UTC
    eliminados = []
    for volcado in volcados[retener:]:
        base = volcado.name.removesuffix(".dump")
        for ruta in (volcado, destino / f"{base}.manifiesto.json", destino / f"{base}.restauracion.json"):
            if ruta.exists():
                ruta.unlink()
        eliminados.append(volcado.name)
    return eliminados


def descartar_incompletos(destino: Path, marca: str) -> list[str]:
    """Borra lo que una ejecución fallida dejó a medias en el volumen: el
    `.dump.parcial` (p. ej. cortado por falta de espacio) y, si llegó a
    escribirse, el `.manifiesto.json.parcial` de esa misma marca, para que no
    siga ocupando el volumen lleno.

    Solo borra `.parcial`: un respaldo terminado con la misma marca (otra
    ejecución en el mismo segundo) no es de esta ejecución y queda intacto.
    Antes se borraban `coldcare_<marca>.dump` y su manifiesto, y un fallo en
    el mismo segundo que un respaldo correcto se llevaba el respaldo bueno.

    Nunca lanza: corre dentro del manejo del fallo, antes de auditarlo."""
    descartados = []
    for ruta in map(ruta_parcial, archivos_respaldo(destino, marca)):
        try:
            if not ruta.is_file():
                continue
            ruta.unlink()
        except OSError as exc:
            logger.warning("No se pudo borrar %s, que dejó incompleto el respaldo fallido: %s", ruta.name, exc)
            continue
        logger.warning("Se borró %s, que dejó incompleto el respaldo fallido.", ruta.name)
        descartados.append(ruta.name)
    return descartados


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(prog="respaldo_postgres")
    parser.add_argument("--destino", type=Path, default=Path(os.environ.get("RESPALDO_DESTINO", "respaldos")))
    parser.add_argument(
        "--retener", type=int, default=int(os.environ.get("RESPALDO_RETENER", "0")),
        help="Respaldos a conservar en --destino (0 = todos)",
    )
    args = parser.parse_args(argv)
    database_url = get_settings().database_url
    marca = marca_utc()
    try:
        manifiesto = asyncio.run(respaldar(database_url, args.destino, marca))
    except Exception as exc:
        motivo = exc.stderr.strip()[:300] if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)[:300]
        logger.critical("RESPALDO FALLIDO: %s", motivo)
        descartar_incompletos(args.destino, marca)
        try:
            asyncio.run(_auditar(database_url, "RESPALDO_FALLIDO", {"error": motivo}))
        except Exception:
            logger.critical("RESPALDO FALLIDO y no se pudo registrar en audit_logs.")
        return CODIGO_FALLO
    # Solo después de un respaldo correcto: un fallo nunca deja el volumen sin copias.
    eliminados = podar_respaldos(args.destino, args.retener)
    asyncio.run(_auditar(database_url, "RESPALDO_COMPLETADO", {
        "archivo": manifiesto["archivo"], "sha256": manifiesto["sha256"],
        "filas": {t: h["filas"] for t, h in manifiesto["huellas"].items()},
        "retenidos": args.retener or None, "eliminados": eliminados,
    }))
    logger.info("Respaldo completado: %s (%d bytes)", manifiesto["archivo"], manifiesto["bytes"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
