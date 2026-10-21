"""HU-12 + HU-19: varios dispositivos publicando a la vez, API consultada en
paralelo y latencia recepción→emisión SSE, contra el stack local real.

Uso:  python scripts/hu12_hu19_carga.py [dispositivos] [lecturas]

Da de alta los dispositivos de prueba FARM-1N-TEST en el backend y emite sus
credenciales MQTT (BE-002), inicia sesión como el farmacéutico demo
(usuario de scripts/seed_dev.py, contraseña en .env),
abre el stream SSE y mide. No imprime secretos.
"""
import asyncio
import json
import random
import ssl
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiomqtt
import httpx
from aprovisionar import aprovisionar, cabecera_admin, credenciales_semilla

RAIZ = Path(__file__).resolve().parent.parent
ENV = dict(linea.split("=", 1) for linea in (RAIZ / ".env").read_text().splitlines() if "=" in linea)
API = "http://localhost:8000"
N_DISP = int(sys.argv[1]) if len(sys.argv) > 1 else 5
N_LECT = int(sys.argv[2]) if len(sys.argv) > 2 else 10


def credenciales_demo() -> tuple[str, str]:
    """Farmacéutico demo de seed_dev.py; su contraseña está en .env (BE-023)."""
    return credenciales_semilla("Farmaceutico Demo")


async def asegurar_dispositivos(_http: httpx.AsyncClient) -> dict[str, str]:
    """BE-002: credenciales emitidas por el backend, no creadas en EMQX."""
    tokens = {}
    with httpx.Client(timeout=15) as http:
        cabecera = cabecera_admin(http)
        for i in range(1, N_DISP + 1):
            dev = f"FARM-{10 + i}-TEST"
            tokens[dev] = aprovisionar(http, cabecera, dev, "prueba de carga HU-12/HU-19")
    return tokens


def lectura(dev: str, boot: int, seq: int) -> dict:
    """Telemetría en el contrato v1.0 del firmware ColdCare1023/IoT."""
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "schema_version": "1.0", "device_id": dev, "boot_id": boot, "seq_no": seq + 1,
        "captured_at": ahora, "time_quality": "sntp_synced", "uptime_s": seq * 30,
        "temp_ds18b20": round(random.uniform(3, 7), 2), "ds18b20_status": "OK", "ds18b20_fault": None,
        "temp_sht31": 22.0, "humedad_sht31": 55.0, "sht31_status": "OK", "sht31_fault": None,
        "door_open": False, "door_open_s": 0, "door_open_streak_s": None, "mc38_status": "installed",
        "power_source": None, "sensor_fault": False, "firmware_version": "v1.2.0",
    }


def rid(p: dict) -> str:
    return f"{p['device_id']}_{p['boot_id']}_{p['seq_no']}"


async def dispositivo(dev: str, token: str, boot: int, enviados: dict, acks: dict) -> None:
    ctx = ssl.create_default_context(cafile=str(RAIZ / "certs/ca.crt"))
    async with aiomqtt.Client("localhost", 8883, username=dev, password=token, identifier=dev,
                              tls_context=ctx) as c:
        await c.subscribe(f"farmacias/{dev}/ack", qos=1)

        async def escuchar():
            async for m in c.messages:
                a = json.loads(m.payload)
                if a.get("status") == "committed":
                    acks[f"{a['device_id']}_{a['boot_id']}_{a['seq_no']}"] = time.perf_counter()

        oyente = asyncio.create_task(escuchar())
        for seq in range(N_LECT):
            p = lectura(dev, boot, seq)
            enviados[rid(p)] = time.perf_counter()
            await c.publish(f"farmacias/{dev}/lecturas", json.dumps(p), qos=1)
            await asyncio.sleep(1.05)  # timestamps distintos y ritmo de ráfaga
        await asyncio.sleep(5)
        oyente.cancel()


async def sse(http: httpx.AsyncClient, token: str, recibidos: dict, listo: asyncio.Event, fin: asyncio.Event):
    t = (await http.post(f"{API}/api/auth/sse-ticket", headers={"Authorization": f"Bearer {token}"})).json()
    async with http.stream("GET", f"{API}/api/sse/lecturas", params={"ticket": t["ticket"]}, timeout=None) as r:
        listo.set()
        async for linea in r.aiter_lines():
            if linea.startswith("data:"):
                try:
                    datos = json.loads(linea[5:])
                except ValueError:
                    continue
                rid = datos.get("reading_id") if isinstance(datos, dict) else None
                if rid:
                    recibidos.setdefault(rid, time.perf_counter())
            if fin.is_set():
                break


async def api_en_paralelo(http: httpx.AsyncClient, token: str, fin: asyncio.Event, res: list):
    while not fin.is_set():
        t0 = time.perf_counter()
        r = await http.get(f"{API}/api/lecturas", params={"limite": 20},
                           headers={"Authorization": f"Bearer {token}"})
        res.append((r.status_code, (time.perf_counter() - t0) * 1000))
        await asyncio.sleep(0.5)


async def main():
    async with httpx.AsyncClient(timeout=15) as http:
        tokens = await asegurar_dispositivos(http)
        email, clave = credenciales_demo()
        r = await http.post(f"{API}/api/auth/login", data={"username": email, "password": clave})
        r.raise_for_status()
        jwt = r.json()["access_token"]

        enviados, acks, sse_rx, api_res = {}, {}, {}, []
        listo, fin = asyncio.Event(), asyncio.Event()
        t_sse = asyncio.create_task(sse(http, jwt, sse_rx, listo, fin))
        await asyncio.wait_for(listo.wait(), 10)
        t_api = asyncio.create_task(api_en_paralelo(http, jwt, fin, api_res))
        boot = random.randint(1000, 9999)
        await asyncio.gather(*(dispositivo(d, t, boot, enviados, acks) for d, t in tokens.items()))
        fin.set()
        await asyncio.sleep(1)
        t_api.cancel()
        t_sse.cancel()

        # Persistencia: cada lectura enviada debe estar en PostgreSQL vía la API.
        persistidas = 0
        for dev in tokens:
            r = await http.get(f"{API}/api/lecturas", params={"device_id": dev, "limite": 1000},
                               headers={"Authorization": f"Bearer {jwt}"})
            persistidas += sum(1 for x in r.json() if x.get("reading_id", "").startswith(f"{dev}_{boot}_"))

    total = len(enviados)
    lat_ack = [(acks[k] - enviados[k]) * 1000 for k in enviados if k in acks]
    lat_sse = [(sse_rx[k] - enviados[k]) * 1000 for k in enviados if k in sse_rx]
    ok_api = sum(1 for s, _ in api_res if s == 200)

    def resumen(xs):
        if not xs:
            return "sin datos"
        xs = sorted(xs)
        return (f"n={len(xs)} p50={statistics.median(xs):.0f} ms  p95={xs[int(len(xs) * 0.95) - 1]:.0f} ms"
                f"  max={xs[-1]:.0f} ms")

    print(f"Dispositivos simultáneos: {len(tokens)}  ·  lecturas enviadas: {total}  (boot {boot})")
    print(f"HU-12 persistidas en PostgreSQL: {persistidas}/{total}")
    print(f"HU-12 acuses lógicos recibidos:  {len(lat_ack)}/{total}  ·  {resumen(lat_ack)}")
    print(f"HU-12 API en paralelo:           {ok_api}/{len(api_res)} respuestas 200  ·  "
          f"{resumen([ms for _, ms in api_res])}")
    print(f"HU-19 eventos SSE recibidos:     {len(lat_sse)}/{total}")
    print(f"HU-19 latencia publicación→SSE:  {resumen(lat_sse)}")


asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
