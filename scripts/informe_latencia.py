"""LAT-02 / RIE-03: latencia del aviso por correo (p50, p95, p99, máximo).

Solo cuenta el canal de correo. `--device-id` filtra por el `device_id` del detalle,
que existe desde que el despachador lo guarda (las filas anteriores no lo traen).
Lee `NOTIFICACION_ENVIADA.latencia_s` (segundos entre la recepción de la
lectura y el envío del correo) de `audit_logs` y la resume. Con `--desde`
acota la corrida y con `--device-id` la limita a un dispositivo de prueba.

Uso:
    export DATABASE_URL=postgresql+asyncpg://...
    python -m scripts.informe_latencia --desde 2026-11-01T15:00:00Z --device-id LAT02-RUN1
    python -m scripts.informe_latencia --csv docs/evidencias/logs/lat02.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def percentil(valores_ordenados: list[float], p: float) -> float:
    """Percentil por rango más cercano (el que se reporta en el paper)."""
    if not valores_ordenados:
        raise ValueError("Sin muestras.")
    rango = max(1, math.ceil(p / 100 * len(valores_ordenados)))
    return valores_ordenados[rango - 1]


def resumir(latencias: list[float | None]) -> dict:
    """p50/p95/p99/máximo de las latencias medidas; cuenta aparte las sin medir."""
    medidas = sorted(v for v in latencias if v is not None)
    resumen = {"muestras": len(latencias), "medidas": len(medidas), "sin_medir": len(latencias) - len(medidas)}
    if medidas:
        resumen.update({
            "p50_s": percentil(medidas, 50), "p95_s": percentil(medidas, 95),
            "p99_s": percentil(medidas, 99), "max_s": medidas[-1], "min_s": medidas[0],
        })
    return resumen


def seleccionar(filas: list[tuple], device_id: str | None) -> list[dict]:
    """Filas (creado, detalle) de NOTIFICACION_ENVIADA -> avisos de correo de la corrida."""
    salida = []
    for creado, detalle in filas:
        d = json.loads(detalle) if isinstance(detalle, str) else (detalle or {})
        # El SMS también deja NOTIFICACION_ENVIADA (sin latencia): solo cuenta el correo.
        # Las filas anteriores a este campo no traen `canal`: se asumen de correo.
        if d.get("canal", "email") != "email":
            continue
        if device_id and d.get("device_id") != device_id:
            continue
        salida.append({
            "creado": creado.isoformat(), "device_id": d.get("device_id"),
            "latencia_s": d.get("latencia_s"), "diferida": d.get("diferida"),
        })
    return salida


async def _leer(url: str, desde: str | None, device_id: str | None) -> list[dict]:
    condiciones, parametros = ["accion = 'NOTIFICACION_ENVIADA'"], {}
    if desde:
        condiciones.append("created_at >= CAST(:desde AS TIMESTAMPTZ)")
        parametros["desde"] = desde
    motor = create_async_engine(url)
    async with motor.connect() as conexion:
        filas = (await conexion.execute(
            text(f"SELECT created_at, detalle FROM audit_logs WHERE {' AND '.join(condiciones)} ORDER BY created_at"),
            parametros,
        )).all()
    await motor.dispose()
    return seleccionar(filas, device_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--desde")
    parser.add_argument("--device-id")
    parser.add_argument("--csv", help="Guarda una fila por aviso")
    args = parser.parse_args(argv)
    url = os.getenv("DATABASE_URL", "")
    if not url:
        sys.exit("Falta DATABASE_URL.")
    filas = asyncio.run(_leer(url, args.desde, args.device_id))
    resumen = resumir([f["latencia_s"] for f in filas])
    for clave, valor in resumen.items():
        print(f"{clave:>10}: {valor}")
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as salida:
            escritor = csv.DictWriter(salida, fieldnames=["creado", "device_id", "latencia_s", "diferida"])
            escritor.writeheader()
            escritor.writerows(filas)
        print(f"{len(filas)} filas en {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
