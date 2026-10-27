"""HU-29: prueba de restauración controlada en un entorno AISLADO.

Uso:

    RESTAURACION_DATABASE_URL=postgresql://.../coldcare_restore \
    python -m scripts.verificar_restauracion --manifiesto respaldos/coldcare_<UTC>.manifiesto.json

1. Comprueba el SHA-256 del archivo de respaldo contra el manifiesto.
2. `pg_restore --clean --if-exists` sobre RESTAURACION_DATABASE_URL, que debe
   ser una base distinta de DATABASE_URL (el script se niega si coinciden).
3. Calcula la huella de `thermal_readings`, `traceability_records` y
   `audit_logs` en la base restaurada y la compara con la del manifiesto.
4. Escribe el informe JSON junto al manifiesto (`.restauracion.json`) como
   evidencia de HU-29, y sale con 0 solo si todo coincide.
"""

import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts.respaldo_postgres import CODIGO_FALLO, _huellas, sha256_archivo, url_libpq
from src.infrastructure.config import normalizar_database_url
from src.infrastructure.respaldo.huellas import comparar_huellas

logger = logging.getLogger("restauracion")


def restaurar(archivo: Path, url_destino: str) -> None:
    if shutil.which("pg_restore") is None:
        raise RuntimeError("pg_restore no está instalado")
    subprocess.run(
        ["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges",
         f"--dbname={url_libpq(url_destino)}", str(archivo)],
        check=True, capture_output=True, text=True, timeout=3600,
    )


def verificar(manifiesto_ruta: Path, url_destino: str, url_produccion: str | None) -> dict:
    if url_produccion and normalizar_database_url(url_destino) == normalizar_database_url(url_produccion):
        raise RuntimeError("RESTAURACION_DATABASE_URL apunta a la base de producción: use un entorno aislado")
    manifiesto = json.loads(manifiesto_ruta.read_text(encoding="utf-8"))
    archivo = manifiesto_ruta.parent / manifiesto["archivo"]
    integro = sha256_archivo(archivo) == manifiesto["sha256"]
    informe = {
        "manifiesto": manifiesto_ruta.name,
        "archivo": manifiesto["archivo"],
        "archivo_integro": integro,
        "verificado_en": datetime.now(tz=timezone.utc).isoformat(),
    }
    if not integro:
        return {**informe, "coincide": False, "motivo": "el SHA-256 del respaldo no coincide con el manifiesto"}
    restaurar(archivo, url_destino)
    comparacion = comparar_huellas(manifiesto["huellas"], asyncio.run(_huellas(normalizar_database_url(url_destino))))
    return {**informe, **comparacion}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(prog="verificar_restauracion")
    parser.add_argument("--manifiesto", type=Path, required=True)
    args = parser.parse_args(argv)
    url_destino = os.environ.get("RESTAURACION_DATABASE_URL")
    if not url_destino:
        logger.critical("Falta RESTAURACION_DATABASE_URL (base aislada para la prueba).")
        return CODIGO_FALLO
    try:
        informe = verificar(args.manifiesto, url_destino, os.environ.get("DATABASE_URL"))
    except Exception as exc:
        logger.critical("PRUEBA DE RESTAURACIÓN FALLIDA: %s", str(exc)[:300])
        return CODIGO_FALLO
    salida = args.manifiesto.with_suffix("").with_suffix(".restauracion.json")
    salida.write_text(json.dumps(informe, indent=2), encoding="utf-8")
    if not informe["coincide"]:
        logger.critical("La base restaurada NO coincide con el respaldo: %s", salida)
        return CODIGO_FALLO
    logger.info("Restauración verificada: filas y huellas coinciden (%s)", salida)
    return 0


if __name__ == "__main__":
    sys.exit(main())
