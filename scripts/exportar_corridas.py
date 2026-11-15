"""P-1: exporta corridas de banco de `thermal_readings` a CSV para el pipeline preventivo.

Salida (lo que lee `ingesta.importar_coldcare_csv`, en este orden):

    device_id, timestamp, temperatura_interna, apertura_refrigerador, time_quality,
    ds18b20_status, sensor_fault, corrida_id,
    temperatura_ambiental, humedad_ambiental, boot_id, seq_no, origen_hora

- `timestamp` = COALESCE(timestamp_estimado, timestamp): la hora de captura, o la reconstruida si el
  nodo arrancó sin reloj (HU-31). `origen_hora` dice cuál es: `nodo`, `reconstruida` o `recepcion`
  (sin hora ni reconstrucción: es la de llegada).
- `time_quality` es el valor interno (`synced`, `backfilled`, `unsynced`); el pipeline excluye y
  cuenta las `unsynced`, también las reconstruidas.
- `ds18b20_status` = `SENSOR_FAULT` si la lectura no trae temperatura interna, `OK` si la trae.
  `sensor_fault` es el del contrato v1 (DS18B20 o SHT31), vacío en firmware anterior; el pipeline
  usa `ds18b20_status` y solo recurre a `sensor_fault` si falta.
- `temperatura_ambiental` y `humedad_ambiental` (SHT31) son para la ablación con y sin ese sensor.

`corrida_id` no es una columna de la base. Sale de un manifiesto CSV con una fila por hoja de
corrida (plan de pruebas de IA, sección 3.2), sin cambiar el esquema:

    corrida_id,device_id,inicio,fin,tipo,uso,temperatura_inicial,mqtt_host,responsable,observaciones
    C01,ESP32_BANCO_01,2026-11-20T14:00:00Z,2026-11-20T16:05:00Z,S-C,entrenamiento,4.8,192.168.18.21,DS,

Obligatorias: `corrida_id`, `device_id`, `inicio`, `fin` (UTC explícita: `Z` o `+00:00`). La ventana es
[inicio, fin). Dos corridas del mismo dispositivo no pueden solaparse. `uso` es opcional:
`entrenamiento`, `prueba` o `excluida` (se omite, p. ej. S-B). El resumen da la lista de prueba para
`prediccion_preventiva corridas --prueba`.

Solo se exportan lecturas del nodo (`origen = 'dispositivo'`) sin `simulated`. La conexión es de solo
lectura: PostgreSQL con `default_transaction_read_only` y `SET TRANSACTION READ ONLY`; SQLite abierto
con `mode=ro`. El script nunca escribe en la base.

Uso (desde la raíz del backend):
    export DATABASE_URL=postgresql+asyncpg://...      # no se imprime
    python -m scripts.exportar_corridas --manifiesto hojas.csv --salida corridas.csv [--resumen resumen.json]
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.domain.value_objects.device_id import PATRON_DEVICE_ID
from src.infrastructure.database.models import ThermalReadingModel as T

COLUMNAS = (
    "device_id", "timestamp", "temperatura_interna", "apertura_refrigerador", "time_quality",
    "ds18b20_status", "sensor_fault", "corrida_id",
    "temperatura_ambiental", "humedad_ambiental", "boot_id", "seq_no", "origen_hora",
)
OBLIGATORIAS_MANIFIESTO = ("corrida_id", "device_id", "inicio", "fin")
OPCIONALES_MANIFIESTO = ("tipo", "uso", "temperatura_inicial", "mqtt_host", "responsable", "observaciones")
USOS = ("entrenamiento", "prueba", "excluida")
# Sin `#`: el pipeline arma la serie como `device_id#corrida_id` y la vuelve a partir por `#`.
PATRON_CORRIDA = re.compile(r"^[A-Za-z0-9_.-]{1,50}$")
HORA_CAPTURA = func.coalesce(T.timestamp_estimado, T.timestamp)


class ErrorEntrada(ValueError):
    """Manifiesto, destino o base inválidos: no se exporta nada."""


@dataclass(frozen=True)
class Corrida:
    corrida_id: str
    device_id: str
    inicio: datetime
    fin: datetime
    tipo: str = ""
    uso: str = ""
    extra: dict = field(default_factory=dict, compare=False)


def _a_utc(texto: str, columna: str) -> datetime:
    try:
        valor = datetime.fromisoformat(texto.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ErrorEntrada(f"{columna}={texto!r} no es ISO 8601") from None
    if valor.tzinfo is None:
        raise ErrorEntrada(f"{columna}={texto!r} no tiene zona: use UTC explícita (…Z o +00:00)")
    return valor.astimezone(timezone.utc)


def leer_manifiesto(ruta: Path) -> list[Corrida]:
    with ruta.open(encoding="utf-8-sig", newline="") as archivo:
        lector = csv.DictReader(archivo)
        columnas = [(c or "").strip() for c in (lector.fieldnames or [])]
        faltan = [c for c in OBLIGATORIAS_MANIFIESTO if c not in columnas]
        if faltan:
            raise ErrorEntrada(f"{ruta.name}: faltan columnas {faltan}")
        desconocidas = sorted(set(columnas) - set(OBLIGATORIAS_MANIFIESTO) - set(OPCIONALES_MANIFIESTO))
        if desconocidas:
            raise ErrorEntrada(f"{ruta.name}: columnas desconocidas {desconocidas}")
        filas = [{(k or "").strip(): (v or "").strip() for k, v in fila.items()} for fila in lector]
    corridas: list[Corrida] = []
    vistas: set[str] = set()
    for numero, fila in enumerate(filas, start=2):
        if not any(fila.values()):
            continue
        try:
            corrida_id, device_id = fila["corrida_id"], fila["device_id"]
            if not PATRON_CORRIDA.fullmatch(corrida_id):
                raise ErrorEntrada(f"corrida_id {corrida_id!r} inválido (letras, dígitos, _ . -; máx. 50)")
            if corrida_id in vistas:
                raise ErrorEntrada(f"corrida_id {corrida_id} repetido")
            if not re.fullmatch(PATRON_DEVICE_ID, device_id):
                raise ErrorEntrada(f"device_id {device_id!r} inválido")
            inicio, fin = _a_utc(fila["inicio"], "inicio"), _a_utc(fila["fin"], "fin")
            if fin <= inicio:
                raise ErrorEntrada("fin debe ser posterior a inicio")
            uso = fila.get("uso", "").lower()
            if uso and uso not in USOS:
                raise ErrorEntrada(f"uso {uso!r} inválido ({', '.join(USOS)} o vacío)")
        except ErrorEntrada as error:
            raise ErrorEntrada(f"{ruta.name}, fila {numero}: {error}") from None
        vistas.add(corrida_id)
        extra = {c: fila[c] for c in OPCIONALES_MANIFIESTO if fila.get(c) and c not in ("tipo", "uso")}
        corridas.append(Corrida(corrida_id, device_id, inicio, fin, fila.get("tipo", ""), uso, extra))
    if not corridas:
        raise ErrorEntrada(f"{ruta.name} no tiene corridas")
    corridas.sort(key=lambda c: (c.device_id, c.inicio))
    for anterior, siguiente in pairwise(corridas):
        if anterior.device_id == siguiente.device_id and anterior.fin > siguiente.inicio:
            raise ErrorEntrada(
                f"{ruta.name}: {anterior.corrida_id} y {siguiente.corrida_id} se solapan en {anterior.device_id}"
            )
    return corridas


def motor_solo_lectura(url: str) -> AsyncEngine:
    """Motor que no puede escribir. Rechaza dialectos que no sabe poner en solo lectura."""
    destino = make_url(url)
    backend = destino.get_backend_name()
    if backend == "postgresql":
        if destino.get_driver_name() != "asyncpg":
            raise ErrorEntrada("PostgreSQL debe usarse con postgresql+asyncpg://")
        return create_async_engine(destino, connect_args={"server_settings": {"default_transaction_read_only": "on"}})
    if backend == "sqlite":
        base = destino.database or ""
        if base in ("", ":memory:") or base.startswith("file:"):
            raise ErrorEntrada("SQLite debe ser un archivo (sqlite+aiosqlite:///ruta.db)")
        ruta = Path(base).resolve()
        if not ruta.is_file():
            raise ErrorEntrada("la base SQLite indicada no existe")
        import aiosqlite

        # URI `file:` con `mode=ro` (as_uri codifica espacios y caracteres especiales de la ruta).
        uri = f"{ruta.as_uri()}?mode=ro"
        return create_async_engine("sqlite+aiosqlite://", async_creator=lambda: aiosqlite.connect(uri, uri=True))
    raise ErrorEntrada("solo PostgreSQL (asyncpg) o SQLite (aiosqlite)")


def _utc(valor: datetime) -> datetime:
    # SQLite devuelve datetimes sin zona; en esta base siempre son UTC.
    return valor.replace(tzinfo=timezone.utc) if valor.tzinfo is None else valor.astimezone(timezone.utc)


def _texto(valor: object) -> str:
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "true" if valor else "false"
    return str(valor)


def _es_simulada(payload: dict | None) -> bool:
    valor = (payload or {}).get("simulated")
    return valor is True or (isinstance(valor, str) and valor.strip().lower() == "true")


def _origen_hora(time_quality: str | None, estimado: datetime | None) -> str:
    if time_quality == "unsynced":
        return "recepcion" if estimado is None else "reconstruida"
    return "nodo"


def fila_csv(registro, corrida_id: str) -> dict[str, str]:
    payload = registro.payload or {}
    temperatura = registro.temperatura_interna
    sensor_fault = payload.get("sensor_fault")
    return {
        "device_id": registro.device_id,
        "timestamp": _utc(registro.hora).isoformat(timespec="milliseconds"),
        "temperatura_interna": _texto(temperatura),
        "apertura_refrigerador": _texto(registro.apertura_refrigerador),
        "time_quality": _texto(registro.time_quality),
        "ds18b20_status": "SENSOR_FAULT" if temperatura is None else "OK",
        "sensor_fault": _texto(sensor_fault) if isinstance(sensor_fault, bool) else "",
        "corrida_id": corrida_id,
        "temperatura_ambiental": _texto(registro.temperatura_ambiental),
        "humedad_ambiental": _texto(registro.humedad_ambiental),
        "boot_id": _texto(registro.boot_id),
        "seq_no": _texto(registro.seq_no),
        "origen_hora": _origen_hora(registro.time_quality, registro.timestamp_estimado),
    }


def consulta(corrida: Corrida):
    return (
        select(
            T.device_id, HORA_CAPTURA.label("hora"), T.temperatura_interna, T.apertura_refrigerador,
            T.time_quality, T.temperatura_ambiental, T.humedad_ambiental, T.boot_id, T.seq_no,
            T.timestamp_estimado, T.payload,
        )
        .where(
            T.device_id == corrida.device_id,
            T.origen == "dispositivo",
            HORA_CAPTURA >= corrida.inicio,
            HORA_CAPTURA < corrida.fin,
        )
        .order_by(HORA_CAPTURA, T.boot_id, T.seq_no)
    )


async def exportar(url: str, corridas: list[Corrida], salida: Path) -> dict:
    """Escribe el CSV (atómico: primero `<salida>.parcial`) y devuelve el resumen por corrida."""
    motor = motor_solo_lectura(url)
    parcial = salida.with_name(salida.name + ".parcial")
    resumen: dict = {"columnas": list(COLUMNAS), "corridas": [], "omitidas_por_uso": []}
    try:
        with parcial.open("w", encoding="utf-8", newline="") as archivo:
            escritor = csv.DictWriter(archivo, fieldnames=COLUMNAS, lineterminator="\n")
            escritor.writeheader()
            async with motor.connect() as conexion:
                if motor.dialect.name == "postgresql":
                    await conexion.execute(text("SET TRANSACTION READ ONLY"))
                for corrida in corridas:
                    if corrida.uso == "excluida":
                        resumen["omitidas_por_uso"].append(corrida.corrida_id)
                        continue
                    cuenta = {"filas": 0, "ds18b20_sensor_fault": 0, "unsynced": 0, "simuladas_omitidas": 0}
                    primera = ultima = None
                    resultado = await conexion.stream(consulta(corrida))
                    async for registro in resultado:
                        if _es_simulada(registro.payload):
                            cuenta["simuladas_omitidas"] += 1
                            continue
                        fila = fila_csv(registro, corrida.corrida_id)
                        escritor.writerow(fila)
                        cuenta["filas"] += 1
                        cuenta["ds18b20_sensor_fault"] += fila["ds18b20_status"] == "SENSOR_FAULT"
                        cuenta["unsynced"] += fila["time_quality"] == "unsynced"
                        primera = primera or fila["timestamp"]
                        ultima = fila["timestamp"]
                    resumen["corridas"].append({
                        "corrida_id": corrida.corrida_id, "device_id": corrida.device_id,
                        "inicio": corrida.inicio.isoformat(), "fin": corrida.fin.isoformat(),
                        "tipo": corrida.tipo, "uso": corrida.uso, **corrida.extra, **cuenta,
                        "primera_lectura": primera, "ultima_lectura": ultima,
                    })
                await conexion.rollback()
        parcial.replace(salida)
    except BaseException:
        parcial.unlink(missing_ok=True)
        raise
    finally:
        await motor.dispose()
    resumen["filas"] = sum(c["filas"] for c in resumen["corridas"])
    resumen["corridas_sin_lecturas"] = [c["corrida_id"] for c in resumen["corridas"] if c["filas"] == 0]
    resumen["prueba"] = ",".join(c["corrida_id"] for c in resumen["corridas"] if c["uso"] == "prueba")
    return resumen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="exportar_corridas", description=__doc__.splitlines()[0])
    parser.add_argument("--manifiesto", type=Path, required=True, help="hojas de corrida (CSV)")
    parser.add_argument("--salida", type=Path, required=True, help="CSV para `prediccion_preventiva corridas`")
    parser.add_argument("--resumen", type=Path, default=None, help="resumen por corrida (JSON)")
    parser.add_argument("--sobrescribir", action="store_true")
    parser.add_argument("--database-url", default=None, help="por defecto DATABASE_URL (no se imprime)")
    args = parser.parse_args(argv)
    url = args.database_url or os.environ.get("DATABASE_URL")
    try:
        if not url:
            raise ErrorEntrada("defina DATABASE_URL o --database-url")
        if args.salida.exists() and not args.sobrescribir:
            raise ErrorEntrada(f"{args.salida} ya existe (use --sobrescribir)")
        corridas = leer_manifiesto(args.manifiesto)
        resumen = asyncio.run(exportar(url, corridas, args.salida))
    except (ErrorEntrada, OSError) as error:
        print(f"No se exporta nada: {error}", file=sys.stderr)
        return 2
    texto = json.dumps(resumen, indent=2, ensure_ascii=False)
    if args.resumen:
        args.resumen.write_text(texto + "\n", encoding="utf-8")
    print(texto)
    if resumen["corridas_sin_lecturas"]:
        print(f"Aviso: corridas sin lecturas: {', '.join(resumen['corridas_sin_lecturas'])}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
