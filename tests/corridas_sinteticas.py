"""CSV SINTÉTICO de corridas controladas, con las columnas del exportador P-1.

Solo comprueba el método (partición, umbral, registro). Nunca se reporta como
resultado: las cifras reales salen de las corridas en hardware."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

INICIO = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)
DISPOSITIVO = "ESP32_BANCO_01"


def escribir_corridas(
    ruta: Path,
    corridas: int = 5,
    con_excursion: set[int] | None = None,
    separacion_h: float = 2.0,
    semilla: int = 7,
) -> Path:
    """`corridas` corridas de 4 h cada 30 s (C1, C2, ...). Las de
    `con_excursion` (números desde 1; por defecto todas) suben hasta ~11 °C
    entre los minutos 150 y 200 y vuelven al rango."""
    con_excursion = set(range(1, corridas + 1)) if con_excursion is None else con_excursion
    rng = np.random.default_rng(semilla)
    filas = []
    for c in range(1, corridas + 1):
        t0 = INICIO + timedelta(hours=(c - 1) * (4 + separacion_h))
        for i in range(4 * 120):
            minuto = i / 2
            temp = 5.0 + rng.normal(0, 0.05)
            if c in con_excursion:
                if 150 <= minuto < 200:
                    temp += (minuto - 150) * 0.12
                elif 200 <= minuto < 220:
                    temp += max(0.0, 6.0 - (minuto - 200) * 0.3)
            filas.append({
                "device_id": DISPOSITIVO,
                "timestamp": (t0 + timedelta(seconds=30 * i)).isoformat(timespec="milliseconds"),
                "temperatura_interna": round(temp, 3),
                "apertura_refrigerador": "false",
                "time_quality": "synced",
                "ds18b20_status": "OK",
                "sensor_fault": "false",
                "corrida_id": f"C{c}",
                "temperatura_ambiental": round(22.0 + 0.02 * max(0.0, minuto - 140), 2),
                "humedad_ambiental": 55.0,
                "boot_id": f"boot-{c}",
                "seq_no": i,
                "origen_hora": "nodo",
            })
    pd.DataFrame(filas).to_csv(ruta, index=False)
    return ruta
