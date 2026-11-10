"""Paper (banco): vigila thermal_alerts y anota CUÁNDO se ve abrir/cerrar cada episodio.

La tabla no guarda la hora de una reapertura (dentro de los 15 min de cooldown se reusa la misma
fila y `ultima_actualizacion` es la hora de captura), así que la latencia "recepción → alerta" se
mide desde fuera: se consulta el episodio abierto cada ~0.3 s y se anota la hora de la laptop al
ver el cambio. Al terminar se cruza cada apertura con su lectura (received_at, created_at).

Escribe <carpeta>/alertas_vistas.csv. Solo lee la base.

Uso (desde infra-local/):
    python scripts/paper_vigia_alertas.py <carpeta> <segundos> [--device ESP32_FARMACIA_01]
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_corrida import DEVICE, ISO, PG, psql_csv


def episodio_abierto(dev: str) -> tuple[str, str, str] | None:
    sql = ("SELECT id, nivel_riesgo, lectura_mas_reciente_id FROM thermal_alerts "
           f"WHERE device_id = '{dev}' AND episodio_abierto = 1 ORDER BY ultima_actualizacion DESC LIMIT 1")
    out = subprocess.run(
        ["docker", "exec", PG, "sh", "-c", f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -AtF, -c "{sql}"'],
        capture_output=True, text=True, encoding="utf-8", check=False).stdout.strip()
    return tuple(out.split(",")) if out else None  # type: ignore[return-value]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("carpeta", type=Path)
    p.add_argument("segundos", type=float)
    p.add_argument("--device", default=DEVICE)
    a = p.parse_args()

    eventos, previo = [], None
    fin = time.monotonic() + a.segundos
    while time.monotonic() < fin:
        actual = episodio_abierto(a.device)
        clave = (actual[0], actual[1]) if actual else None
        if clave != previo:
            visto = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            if previo:
                eventos.append({"visto_utc": visto, "evento": "cierra", "alerta_id": previo[0],
                                "nivel": previo[1], "lectura_id": ""})
            if actual:
                eventos.append({"visto_utc": visto, "evento": "abre", "alerta_id": actual[0],
                                "nivel": actual[1], "lectura_id": actual[2]})
            previo = clave
        time.sleep(0.05)

    ids = sorted({e["lectura_id"] for e in eventos if e["lectura_id"]})
    lect = {}
    if ids:
        lista = ",".join(f"'{i}'" for i in ids)
        for f in psql_csv(
                f"SELECT id, temperatura_interna, to_char(received_at AT TIME ZONE 'UTC', '{ISO}') AS received_utc, "
                f"to_char(created_at AT TIME ZONE 'UTC', '{ISO}') AS commit_utc FROM thermal_readings "
                f"WHERE id IN ({lista})"):
            lect[f["id"]] = f
    for e in eventos:
        f = lect.get(e["lectura_id"], {})
        e.update({"temp_lectura": f.get("temperatura_interna", ""), "received_utc": f.get("received_utc", ""),
                  "commit_utc": f.get("commit_utc", "")})
    campos = ["visto_utc", "evento", "alerta_id", "nivel", "lectura_id", "temp_lectura", "received_utc", "commit_utc"]
    with (a.carpeta / "alertas_vistas.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=campos)
        w.writeheader()
        w.writerows(eventos)
    print(f"{len(eventos)} cambios de episodio en {a.carpeta / 'alertas_vistas.csv'}")


if __name__ == "__main__":
    main()
