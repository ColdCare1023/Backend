"""IA (banco): junta las corridas de evidencia/ml/ en el CSV que lee el pipeline preventivo.

    python scripts/ml_exportar.py [salida.csv]      (por defecto evidencia/ml/corridas.csv)

Columnas (ingesta.importar_coldcare_csv): device_id, timestamp, temperatura_interna,
apertura_refrigerador, corrida_id, time_quality, sensor_fault, temperatura_ambiental,
humedad_ambiental. corrida_id = nombre de la carpeta. Solo corridas cerradas (con fin_utc).
"""

import csv
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ML = RAIZ / "evidencia" / "ml"
salida = Path(sys.argv[1]) if len(sys.argv) > 1 else ML / "corridas.csv"
CAMPOS = ["device_id", "timestamp", "temperatura_interna", "apertura_refrigerador", "corrida_id",
          "time_quality", "sensor_fault", "temperatura_ambiental", "humedad_ambiental"]

filas, resumen = [], []
for carpeta in sorted(p for p in ML.iterdir() if p.is_dir()):
    meta = carpeta / "corrida.json"
    lecturas = carpeta / "lecturas.csv"
    if not meta.exists() or not lecturas.exists():
        continue
    datos = json.loads(meta.read_text(encoding="utf-8"))
    if not datos.get("fin_utc"):
        continue
    n = 0
    with lecturas.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            puerta = {"t": "true", "f": "false"}.get((r.get("puerta_abierta") or "").strip(), "")
            filas.append({
                "device_id": datos["device_id"],
                "timestamp": r["captured_utc"],
                "temperatura_interna": r["temp_ds18b20"],
                "apertura_refrigerador": puerta,
                "corrida_id": carpeta.name,
                "time_quality": r.get("time_quality", ""),
                "sensor_fault": r.get("sensor_fault", ""),
                "temperatura_ambiental": r.get("temp_sht31", ""),
                "humedad_ambiental": r.get("humedad", ""),
            })
            n += 1
    resumen.append((carpeta.name, n))

with salida.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=CAMPOS)
    w.writeheader()
    w.writerows(filas)
for nombre, n in resumen:
    print(f"  {nombre}: {n} lecturas")
print(f"{len(resumen)} corridas, {len(filas)} lecturas -> {salida}")
