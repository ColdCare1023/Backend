"""Publica una lectura de prueba como FARM-02-TEST con campos alterados y muestra el acuse.
Uso: publicar_caso.py '<json con overrides>'   p.ej. '{"humedad_ambiental": 120}'
     publicar_caso.py --crudo '<texto>'        publica el texto tal cual (JSON malformado, etc.)"""
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
    "schema_version": 1, "device_id": DEV, "timestamp": ahora, "reading_id": f"{DEV}_{ahora}",
    "boot_id": 1, "seq_no": int(time.time()) % 1_000_000, "time_quality": "synced",
    "temperatura_interna": 4.5, "estado_temperatura_interna": "ok",
    "temperatura_ambiental": 21.0, "estado_temperatura_ambiental": "ok",
    "humedad_ambiental": 55.0, "estado_humedad_ambiental": "ok",
    "apertura_refrigerador": False, "mc38_status": "ok", "duracion_apertura_segundos": 0,
    "estado_conectividad": "online", "firmware_version": "1.0.0",
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
