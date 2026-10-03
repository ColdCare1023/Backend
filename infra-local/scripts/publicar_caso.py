"""Publica una telemetría v1.0 de prueba como FARM-02-TEST con campos alterados y muestra el acuse.

Uso:  python scripts/publicar_caso.py '<json con overrides>'   p.ej. '{"humedad_sht31": 120}'
      python scripts/publicar_caso.py --crudo '<texto>'        publica el texto tal cual (JSON malformado…)
El valor "__BORRAR__" quita el campo (p.ej. '{"captured_at": "__BORRAR__"}').
"""
import asyncio
import json
import ssl
import sys
import time
from pathlib import Path

import aiomqtt

RAIZ = Path(__file__).resolve().parent.parent
DEV = "FARM-02-TEST"
ahora = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
base = {
    "schema_version": "1.0", "device_id": DEV, "boot_id": 1, "seq_no": int(time.time()) % 1_000_000 + 1,
    "captured_at": ahora, "time_quality": "sntp_synced", "uptime_s": 60,
    "temp_ds18b20": 4.5, "ds18b20_status": "OK", "ds18b20_fault": None,
    "temp_sht31": 21.0, "humedad_sht31": 55.0, "sht31_status": "OK", "sht31_fault": None,
    "door_open": False, "door_open_s": 0, "door_open_streak_s": None, "mc38_status": "installed",
    "power_source": None, "sensor_fault": False, "firmware_version": "v1.2.0",
}
if sys.argv[1] == "--crudo":
    cuerpo = sys.argv[2]
else:
    for k, v in json.loads(sys.argv[1]).items():
        if v == "__BORRAR__":
            base.pop(k, None)
        else:
            base[k] = v
    cuerpo = json.dumps(base)


async def main():
    ctx = ssl.create_default_context(cafile=str(RAIZ / "certs/ca.crt"))
    async with aiomqtt.Client("localhost", 8883, username=DEV, password=(RAIZ / f"secrets/{DEV}.token").read_text(),
                              identifier=DEV, tls_context=ctx) as c:
        await c.subscribe(f"farmacias/{DEV}/ack", qos=1)
        await c.publish(f"farmacias/{DEV}/lecturas", cuerpo, qos=1)
        print("enviado:", cuerpo[:160])
        try:
            m = await asyncio.wait_for(anext(c.messages), 8)
            print("ACK:", m.payload.decode())
        except TimeoutError:
            print("SIN ACK en 8 s")

asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
