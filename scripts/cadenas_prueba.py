"""INT-02/03/04/06/08: cadenas de trazabilidad de prueba, íntegras y alteradas.

Genera N eslabones (10 000 por defecto) con la fórmula v1, v2 o v3 y los
inserta en una base de PRUEBA. `alterar` modifica un eslabón con SQL directo
desactivando antes los triggers de solo-anexado, para comprobar que la
verificación detecta la ruptura.

Salvaguardas: se niega con ENVIRONMENT=production y exige que el nombre de la
base contenga «prueba» o «test»; nunca toca una base de datos real.

Uso:
    export DATABASE_URL=postgresql+asyncpg://.../coldcare_prueba
    python -m scripts.cadenas_prueba generar --version 3 --n 10000 --chain-id PRUEBA-V3
    python -m scripts.cadenas_prueba alterar --chain-id PRUEBA-V3 --seq 5000 --tipo payload
    python -m scripts.cadenas_prueba alterar --chain-id PRUEBA-V3 --seq 5000 --tipo borrado
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.domain.value_objects.hash_encadenado import GENESIS_HASH, hash_esperado, timestamp_canonico

TRIGGERS = (
    ("traceability_records", "traceability_records_sin_borrado"),
    ("traceability_records", "traceability_records_solo_marcas"),
)
TIPOS_ALTERACION = ("payload", "hash", "borrado", "previous_hash")


def generar_eslabones(
    chain_id: str, n: int, version: int, clave: bytes | None, inicio: datetime | None = None
) -> list[dict]:
    """N eslabones consecutivos y válidos para la fórmula `version`. Función pura."""
    if version == 3 and not clave:
        raise ValueError("La versión 3 (HMAC) necesita --clave.")
    inicio = inicio or datetime(2026, 1, 1, tzinfo=timezone.utc)
    filas: list[dict] = []
    previo = GENESIS_HASH
    for i in range(1, n + 1):
        momento = timestamp_canonico(inicio + timedelta(seconds=30 * i))
        payload = {"device_id": chain_id, "seq": i, "temperatura_interna": 4.0 + (i % 7) / 10}
        actual = hash_esperado(version, chain_id, previo, momento, payload, clave)
        filas.append({
            "id": str(uuid.uuid4()), "tipo_evento": "LECTURA_TERMICA", "device_id": chain_id,
            "payload": payload, "timestamp": momento, "previous_hash": previo, "hash_actual": actual,
            "chain_id": chain_id, "chain_seq": i, "hash_version": version,
        })
        previo = actual
    return filas


def verificar_eslabones(filas: list[dict], clave: bytes | None) -> int | None:
    """Posición (chain_seq) del primer eslabón que no cuadra, o None si es íntegra."""
    previo = GENESIS_HASH
    for fila in filas:
        esperado = hash_esperado(
            fila["hash_version"], fila["chain_id"], fila["previous_hash"], fila["timestamp"], fila["payload"], clave
        )
        if fila["previous_hash"] != previo or fila["hash_actual"] != esperado:
            return fila["chain_seq"]
        previo = fila["hash_actual"]
    return None


def _validar_entorno(url: str) -> None:
    if os.getenv("ENVIRONMENT", "development") == "production":
        sys.exit("Se niega a correr con ENVIRONMENT=production.")
    nombre = url.rsplit("/", 1)[-1].split("?")[0].lower()
    if "prueba" not in nombre and "test" not in nombre:
        sys.exit(f"La base «{nombre}» no parece de prueba (debe contener «prueba» o «test»).")


async def _generar(args, url: str) -> None:
    import json

    clave = args.clave.encode() if args.clave else None
    filas = generar_eslabones(args.chain_id, args.n, args.version, clave)
    motor = create_async_engine(url)
    async with motor.begin() as conexion:
        for lote in range(0, len(filas), 1000):
            await conexion.execute(
                text(
                    "INSERT INTO traceability_records (id, tipo_evento, device_id, payload, timestamp, previous_hash,"
                    " hash_actual, chain_id, chain_seq, hash_version, is_corrupted, is_after_corruption)"
                    " VALUES (:id, :tipo_evento, :device_id, CAST(:payload AS JSON), CAST(:timestamp AS TIMESTAMPTZ),"
                    " :previous_hash, :hash_actual, :chain_id, :chain_seq, :hash_version, false, false)"
                ),
                [{**f, "payload": json.dumps(f["payload"])} for f in filas[lote : lote + 1000]],
            )
    await motor.dispose()
    print(f"{len(filas)} eslabones v{args.version} en la cadena {args.chain_id}.")


async def _alterar(args, url: str) -> None:
    motor = create_async_engine(url)
    async with motor.begin() as conexion:
        for tabla, disparador in TRIGGERS:
            await conexion.execute(text(f"ALTER TABLE {tabla} DISABLE TRIGGER {disparador}"))
        try:
            parametros = {"c": args.chain_id, "s": args.seq}
            if args.tipo == "payload":
                sentencia = (
                    "UPDATE traceability_records SET payload = CAST('{\"alterado\": true}' AS JSON)"
                    " WHERE chain_id = :c AND chain_seq = :s"
                )
            elif args.tipo == "hash":
                sentencia = (
                    "UPDATE traceability_records SET hash_actual = repeat('0', 64)"
                    " WHERE chain_id = :c AND chain_seq = :s"
                )
            elif args.tipo == "previous_hash":
                sentencia = (
                    "UPDATE traceability_records SET previous_hash = repeat('0', 64)"
                    " WHERE chain_id = :c AND chain_seq = :s"
                )
            else:
                sentencia = "DELETE FROM traceability_records WHERE chain_id = :c AND chain_seq = :s"
            resultado = await conexion.execute(text(sentencia), parametros)
            print(f"Alteración «{args.tipo}» en {args.chain_id} #{args.seq}: {resultado.rowcount} fila(s).")
        finally:
            for tabla, disparador in TRIGGERS:
                await conexion.execute(text(f"ALTER TABLE {tabla} ENABLE TRIGGER {disparador}"))
    await motor.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="orden", required=True)
    p = sub.add_parser("generar")
    p.add_argument("--version", type=int, choices=(1, 2, 3), default=2)
    p.add_argument("--n", type=int, default=10_000)
    p.add_argument("--chain-id", required=True)
    p.add_argument("--clave", default=os.getenv("TRAZABILIDAD_HMAC_KEY", ""), help="Solo v3 (nunca por argumento en producción)")
    p = sub.add_parser("alterar")
    p.add_argument("--chain-id", required=True)
    p.add_argument("--seq", type=int, required=True)
    p.add_argument("--tipo", choices=TIPOS_ALTERACION, default="payload")
    args = parser.parse_args(argv)
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        sys.exit("DATABASE_URL debe apuntar a una base PostgreSQL de prueba.")
    _validar_entorno(url)
    asyncio.run(_generar(args, url) if args.orden == "generar" else _alterar(args, url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
