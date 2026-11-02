"""CON-02 / ENE-02: huecos y duplicados de seq_no por boot_id.

Sostiene «0 pérdidas»: dentro de un mismo `boot_id` los `seq_no` deben ser
consecutivos y no repetirse. Un hueco es una lectura que nunca llegó; un
duplicado es una identidad usada dos veces.

Uso:
    export DATABASE_URL=postgresql+asyncpg://...
    python -m scripts.huecos_seq --device-id ESP32_FARMACIA_01 [--desde 2026-11-01T00:00:00Z]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from itertools import pairwise

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def analizar(seqs: list[int]) -> dict:
    """Huecos (rangos que faltan entre el menor y el mayor) y duplicados de una lista de seq_no."""
    if not seqs:
        return {"total": 0, "huecos": [], "faltantes": 0, "duplicados": {}}
    cuentas = Counter(seqs)
    unicos = sorted(cuentas)
    huecos = []
    for anterior, siguiente in pairwise(unicos):
        if siguiente - anterior > 1:
            huecos.append((anterior + 1, siguiente - 1))
    return {
        "total": len(seqs),
        "primero": unicos[0],
        "ultimo": unicos[-1],
        "huecos": huecos,
        "faltantes": sum(fin - ini + 1 for ini, fin in huecos),
        "duplicados": {s: n for s, n in cuentas.items() if n > 1},
    }


async def _leer(url: str, device_id: str, desde: str | None) -> dict[int, list[int]]:
    sql = "SELECT boot_id, seq_no FROM thermal_readings WHERE device_id = :d AND boot_id IS NOT NULL AND seq_no IS NOT NULL"
    parametros = {"d": device_id}
    if desde:
        sql += " AND timestamp >= CAST(:desde AS TIMESTAMPTZ)"
        parametros["desde"] = desde
    motor = create_async_engine(url)
    async with motor.connect() as conexion:
        filas = (await conexion.execute(text(sql), parametros)).all()
    await motor.dispose()
    por_arranque: dict[int, list[int]] = {}
    for boot_id, seq_no in filas:
        por_arranque.setdefault(boot_id, []).append(seq_no)
    return por_arranque


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--desde")
    args = parser.parse_args(argv)
    url = os.getenv("DATABASE_URL", "")
    if not url:
        sys.exit("Falta DATABASE_URL.")
    por_arranque = asyncio.run(_leer(url, args.device_id, args.desde))
    perdidas = 0
    for boot_id in sorted(por_arranque):
        r = analizar(por_arranque[boot_id])
        perdidas += r["faltantes"]
        print(f"boot_id={boot_id}: {r['total']} lecturas, seq {r['primero']}..{r['ultimo']}, "
              f"faltantes={r['faltantes']}, duplicados={len(r['duplicados'])}")
        for ini, fin in r["huecos"][:20]:
            print(f"    hueco {ini}..{fin}")
    print(f"TOTAL faltantes: {perdidas}")
    return 0 if perdidas == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
