"""Captura N mensajes crudos de farmacias/+/lecturas y eventos (como backend_service, solo lectura).
Uso: espiar_lecturas.py [n] [timeout_s]"""
import asyncio
import ssl
import sys
from pathlib import Path

import aiomqtt

RAIZ = Path(__file__).resolve().parent.parent
ENV = dict(linea.split("=", 1) for linea in (RAIZ / ".env").read_text().splitlines() if "=" in linea)
N = int(sys.argv[1]) if len(sys.argv) > 1 else 1
T = float(sys.argv[2]) if len(sys.argv) > 2 else 40

async def main():
    ctx = ssl.create_default_context(cafile=str(RAIZ / "certs/ca.crt"))
    async with aiomqtt.Client("localhost", 8883, username="backend_service", password=ENV["MQTT_BACKEND_PASSWORD"],
                              identifier="espia-evidencia", tls_context=ctx) as c:
        await c.subscribe("farmacias/+/lecturas")
        await c.subscribe("farmacias/+/eventos")
        for _ in range(N):
            m = await asyncio.wait_for(anext(c.messages), T)
            print(m.topic, m.payload.decode())

asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
