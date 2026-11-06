"""Paper (banco): registra cada mensaje de lecturas y eventos que llega al broker.

Corre con la imagen del backend (allí están aiomqtt, la CA y la cuenta
backend_service, que solo tiene permiso de lectura sobre estos tópicos). Escribe
una línea CSV por mensaje en stdout, con la hora de llegada al broker, para
contar los reenvíos (misma boot_id+seq_no recibida más de una vez) que la base
no ve porque descarta el duplicado en silencio (HU-11).

Lo lanza `paper_corrida.py inicio` en un contenedor APARTE (`paper-escucha`,
imagen y entorno del backend), para que siga escuchando cuando S6 tumba el
backend. Termina solo al cumplir <segundos> o con `docker rm -f paper-escucha`.
"""

import asyncio
import csv
import json
import os
import ssl
import sys
from datetime import UTC, datetime

import aiomqtt

DEVICE = sys.argv[1] if len(sys.argv) > 1 else "+"
SEGUNDOS = float(sys.argv[2]) if len(sys.argv) > 2 else 3600


async def main() -> None:
    try:
        async with asyncio.timeout(SEGUNDOS):
            await escuchar()
    except TimeoutError:
        pass


async def escuchar() -> None:
    salida = csv.writer(sys.stdout, lineterminator="\n")
    salida.writerow(["llegada_utc", "topico", "boot_id", "seq_no", "captured_at", "tipo", "time_quality", "detalle"])
    sys.stdout.flush()
    # S7 detiene el broker: la escucha se reconecta sola en vez de terminar.
    while True:
        try:
            await sesion(salida)
        except aiomqtt.MqttError as error:
            print(f"# {datetime.now(UTC).isoformat(timespec='seconds')} sin broker: {error}", file=sys.stderr)
            await asyncio.sleep(2)


async def sesion(salida) -> None:
    ctx = ssl.create_default_context(cafile=os.environ["MQTT_CA_FILE"])
    async with aiomqtt.Client(
        os.environ["MQTT_HOST"],
        int(os.environ["MQTT_PORT"]),
        username=os.environ["MQTT_USERNAME"],
        password=os.environ["MQTT_PASSWORD"],
        identifier=f"paper-escucha-{os.getpid()}",
        tls_context=ctx,
    ) as cliente:
        await cliente.subscribe(f"farmacias/{DEVICE}/lecturas", qos=1)
        await cliente.subscribe(f"farmacias/{DEVICE}/eventos", qos=1)
        async for mensaje in cliente.messages:
            llegada = datetime.now(UTC).isoformat(timespec="milliseconds")
            try:
                datos = json.loads(mensaje.payload)
            except (ValueError, TypeError):
                datos = {}
            salida.writerow([
                llegada,
                str(mensaje.topic),
                datos.get("boot_id", ""),
                datos.get("seq_no", ""),
                datos.get("captured_at", datos.get("occurred_at", "")),
                datos.get("event_type", datos.get("tipo", "")),
                datos.get("time_quality", ""),
                json.dumps(datos["detail"], ensure_ascii=False) if isinstance(datos.get("detail"), dict) else "",
            ])
            sys.stdout.flush()


asyncio.run(main())
