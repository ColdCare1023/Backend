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
    python -m scripts.cadenas_prueba insertar --chain-id PRUEBA-V2 --seq 5000        # INT-04
    python -m scripts.cadenas_prueba recomputar --chain-id PRUEBA-V2 --desde 5000    # INT-08
    python -m scripts.cadenas_prueba verificar --chain-id PRUEBA-V2                  # exit 1 si se rompe

`recomputar` es el atacante que, tras alterar un eslabón, rehace los hashes de ahí al final:
con v1/v2 (sin clave) la cadena vuelve a ser coherente y solo un ancla externa lo delata; con v3
exige la clave HMAC, que el atacante no tiene.
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

from scripts._fechas import a_datetime
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
    for esperada, fila in enumerate(filas, start=1):
        if fila["chain_seq"] != esperada:
            return fila["chain_seq"]  # hueco o inserción: la posición no es la que toca
        esperado = hash_esperado(
            fila["hash_version"], fila["chain_id"], fila["previous_hash"], fila["timestamp"], fila["payload"], clave
        )
        if fila["previous_hash"] != previo or fila["hash_actual"] != esperado:
            return fila["chain_seq"]
        previo = fila["hash_actual"]
    return None


def forjar_insercion(filas: list[dict], seq: int, clave: bytes | None) -> list[dict]:
    """INT-04: mete un eslabón forjado en la posición `seq` y corre los demás una
    posición SIN recalcularlos. El nuevo enlaza bien con el anterior, pero el que
    sigue conserva un `previous_hash` que ya no es el suyo."""
    if not 1 <= seq <= len(filas):
        raise ValueError("seq fuera de la cadena.")
    anterior = filas[seq - 2] if seq > 1 else None
    version = filas[0]["hash_version"]
    chain_id = filas[0]["chain_id"]
    previo = anterior["hash_actual"] if anterior else GENESIS_HASH
    momento = filas[seq - 1]["timestamp"]
    payload = {"device_id": chain_id, "forjado": True}
    forjado = {
        "id": str(uuid.uuid4()), "tipo_evento": "LECTURA_TERMICA", "device_id": chain_id, "payload": payload,
        "timestamp": momento, "previous_hash": previo,
        "hash_actual": hash_esperado(version, chain_id, previo, momento, payload, clave),
        "chain_id": chain_id, "chain_seq": seq, "hash_version": version,
    }
    desplazados = [{**f, "chain_seq": f["chain_seq"] + 1} for f in filas[seq - 1 :]]
    return [*filas[: seq - 1], forjado, *desplazados]


def recomputar_desde(filas: list[dict], desde: int, clave: bytes | None) -> list[dict]:
    """INT-08: rehace `previous_hash` y `hash_actual` de `desde` hasta el final, como
    lo haría quien alteró un eslabón. Con v3 necesita la clave HMAC."""
    if filas and filas[0]["hash_version"] >= 3 and not clave:
        raise ValueError("Sin la clave HMAC no se puede recalcular una cadena v3.")
    salida = [dict(f) for f in filas]
    previo = salida[desde - 2]["hash_actual"] if desde > 1 else GENESIS_HASH
    for fila in salida[desde - 1 :]:
        fila["previous_hash"] = previo
        fila["hash_actual"] = hash_esperado(
            fila["hash_version"], fila["chain_id"], previo, fila["timestamp"], fila["payload"], clave
        )
        previo = fila["hash_actual"]
    return salida


def _parametros_fila(fila: dict) -> dict:
    """Parámetros de INSERT: el payload como JSON y el timestamp como datetime (asyncpg no acepta texto)."""
    import json

    return {**fila, "payload": json.dumps(fila["payload"]), "timestamp": a_datetime(fila["timestamp"])}


def _validar_entorno(url: str) -> None:
    if os.getenv("ENVIRONMENT", "development") == "production":
        sys.exit("Se niega a correr con ENVIRONMENT=production.")
    nombre = url.rsplit("/", 1)[-1].split("?")[0].lower()
    if "prueba" not in nombre and "test" not in nombre:
        sys.exit(f"La base «{nombre}» no parece de prueba (debe contener «prueba» o «test»).")


async def _generar(args, url: str) -> None:

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
                [_parametros_fila(f) for f in filas[lote : lote + 1000]],
            )
    await motor.dispose()
    print(f"{len(filas)} eslabones v{args.version} en la cadena {args.chain_id}.")


async def _leer_cadena(conexion, chain_id: str) -> list[dict]:
    import json

    filas = (await conexion.execute(
        text(
            "SELECT id, tipo_evento, device_id, payload, timestamp, previous_hash, hash_actual, chain_id,"
            " chain_seq, hash_version FROM traceability_records WHERE chain_id = :c ORDER BY chain_seq"
        ),
        {"c": chain_id},
    )).all()
    return [
        {
            "id": str(f[0]), "tipo_evento": f[1], "device_id": f[2],
            "payload": json.loads(f[3]) if isinstance(f[3], str) else f[3],
            "timestamp": timestamp_canonico(f[4]), "previous_hash": f[5], "hash_actual": f[6],
            "chain_id": f[7], "chain_seq": f[8], "hash_version": f[9],
        }
        for f in filas
    ]


async def _verificar(args, url: str) -> int:
    clave = args.clave.encode() if args.clave else None
    motor = create_async_engine(url)
    async with motor.connect() as conexion:
        filas = await _leer_cadena(conexion, args.chain_id)
    await motor.dispose()
    roto = verificar_eslabones(filas, clave)
    if roto is None:
        print(f"Cadena {args.chain_id}: ÍNTEGRA ({len(filas)} eslabones).")
        return 0
    print(f"Cadena {args.chain_id}: ROTA en el eslabón #{roto} de {len(filas)}.")
    return 1


async def _reescribir(args, url: str, transformar) -> None:
    """Lee la cadena, aplica `transformar` y la reescribe con los triggers desactivados."""

    clave = args.clave.encode() if args.clave else None
    motor = create_async_engine(url)
    async with motor.begin() as conexion:
        filas = await _leer_cadena(conexion, args.chain_id)
        nuevas = transformar(filas, clave)
        for tabla, disparador in TRIGGERS:
            await conexion.execute(text(f"ALTER TABLE {tabla} DISABLE TRIGGER {disparador}"))
        try:
            # Dos pasos para no chocar con la unicidad (chain_id, chain_seq).
            await conexion.execute(
                text("UPDATE traceability_records SET chain_seq = chain_seq + 10000000 WHERE chain_id = :c"),
                {"c": args.chain_id},
            )
            await conexion.execute(text("DELETE FROM traceability_records WHERE chain_id = :c"), {"c": args.chain_id})
            for lote in range(0, len(nuevas), 1000):
                await conexion.execute(
                    text(
                        "INSERT INTO traceability_records (id, tipo_evento, device_id, payload, timestamp,"
                        " previous_hash, hash_actual, chain_id, chain_seq, hash_version, is_corrupted,"
                        " is_after_corruption) VALUES (:id, :tipo_evento, :device_id, CAST(:payload AS JSON),"
                        " CAST(:timestamp AS TIMESTAMPTZ), :previous_hash, :hash_actual, :chain_id, :chain_seq,"
                        " :hash_version, false, false)"
                    ),
                    [_parametros_fila(f) for f in nuevas[lote : lote + 1000]],
                )
        finally:
            for tabla, disparador in TRIGGERS:
                await conexion.execute(text(f"ALTER TABLE {tabla} ENABLE TRIGGER {disparador}"))
    await motor.dispose()
    print(f"Cadena {args.chain_id} reescrita: {len(nuevas)} eslabones.")


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
    p = sub.add_parser("insertar")
    p.add_argument("--chain-id", required=True)
    p.add_argument("--seq", type=int, required=True)
    p.add_argument("--clave", default=os.getenv("TRAZABILIDAD_HMAC_KEY", ""))
    p = sub.add_parser("recomputar")
    p.add_argument("--chain-id", required=True)
    p.add_argument("--desde", type=int, required=True)
    p.add_argument("--clave", default=os.getenv("TRAZABILIDAD_HMAC_KEY", ""), help="Solo v3")
    p = sub.add_parser("verificar")
    p.add_argument("--chain-id", required=True)
    p.add_argument("--clave", default=os.getenv("TRAZABILIDAD_HMAC_KEY", ""))
    p = sub.add_parser("alterar")
    p.add_argument("--chain-id", required=True)
    p.add_argument("--seq", type=int, required=True)
    p.add_argument("--tipo", choices=TIPOS_ALTERACION, default="payload")
    args = parser.parse_args(argv)
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        sys.exit("DATABASE_URL debe apuntar a una base PostgreSQL de prueba.")
    _validar_entorno(url)
    if args.orden == "verificar":
        return asyncio.run(_verificar(args, url))
    if args.orden == "insertar":
        asyncio.run(_reescribir(args, url, lambda filas, clave: forjar_insercion(filas, args.seq, clave)))
    elif args.orden == "recomputar":
        asyncio.run(_reescribir(args, url, lambda filas, clave: recomputar_desde(filas, args.desde, clave)))
    else:
        asyncio.run(_generar(args, url) if args.orden == "generar" else _alterar(args, url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
