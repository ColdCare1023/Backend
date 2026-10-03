"""Prueba de humo del broker local: TLS, autenticación y ACL (HU-09, HU-10, HU-14).

Uso:  python scripts/smoke_test.py   (con el venv del backend activo)
Lee credenciales de infra-local/.env y secrets/. No imprime secretos.
"""
import asyncio
import socket
import ssl
from pathlib import Path

import aiomqtt

RAIZ = Path(__file__).resolve().parent.parent
ENV = dict(linea.split("=", 1) for linea in (RAIZ / ".env").read_text().splitlines() if "=" in linea)
CA = str(RAIZ / "certs" / "ca.crt")
OTRA_CA = str(RAIZ / "certs" / "otra_ca.crt")
HOST, PUERTO = "localhost", 8883
TOKEN = (RAIZ / "secrets" / "FARM-01-CDL.token").read_text()
TOKEN2 = (RAIZ / "secrets" / "FARM-02-TEST.token").read_text()
BACKEND = ENV["MQTT_BACKEND_PASSWORD"]
resultados: list[tuple[str, bool, str]] = []


def cliente(usuario, clave, ca=CA, host=HOST, puerto=PUERTO, tls=True):
    ctx = ssl.create_default_context(cafile=ca) if tls else None
    return aiomqtt.Client(host, puerto, username=usuario, password=clave,
                          identifier=f"smoke-{usuario}", tls_context=ctx, timeout=5)


def anotar(nombre, ok, detalle=""):
    resultados.append((nombre, ok, detalle))
    print(f"[{'OK ' if ok else 'FAIL'}] {nombre}  {detalle}")


async def debe_fallar(nombre, **kw):
    try:
        async with cliente(**kw):
            anotar(nombre, False, "conectó y no debía")
    except Exception as exc:
        anotar(nombre, True, f"rechazado: {type(exc).__name__}")


async def main():
    # HU-09/10: conexión válida y ciclo lectura -> acuse con el backend real
    try:
        async with cliente("backend_service", BACKEND) as be, cliente("FARM-01-CDL", TOKEN) as nodo:
            await be.subscribe("farmacias/+/lecturas")
            await nodo.subscribe("farmacias/FARM-01-CDL/ack")
            await nodo.publish("farmacias/FARM-01-CDL/lecturas", b'{"x":1}', qos=1)
            msg = await asyncio.wait_for(anext(be.messages), 5)
            anotar("nodo publica QoS1 en su tópico y backend lo recibe", str(msg.topic) == "farmacias/FARM-01-CDL/lecturas")
            await be.publish("farmacias/FARM-01-CDL/ack", b'{"ack":1}', qos=1)
            ack = await asyncio.wait_for(anext(nodo.messages), 5)
            anotar("backend publica acuse y el nodo lo recibe", ack.payload == b'{"ack":1}')

            # HU-10 esc. 3: tópico ajeno denegado (backend no debe recibir nada)
            await nodo.publish("farmacias/FARM-02-TEST/lecturas", b'{"x":2}', qos=1)
            try:
                await asyncio.wait_for(anext(be.messages), 2)
                anotar("ACL: publicar en tópico ajeno", False, "el mensaje llegó")
            except TimeoutError:
                anotar("ACL: publicar en tópico ajeno es descartado", True)
    except Exception as exc:
        anotar("flujo válido", False, repr(exc))

    # HU-10 esc. 2
    await debe_fallar("token inválido rechazado", usuario="FARM-01-CDL", clave="token-malo")
    await debe_fallar("usuario inexistente rechazado", usuario="FANTASMA", clave="x")
    # HU-09 esc. 1: CA que no firmó el broker
    await debe_fallar("CA ajena rechazada", usuario="FARM-01-CDL", clave=TOKEN, ca=OTRA_CA)
    # HU-09 esc. 2: host distinto al del certificado
    try:
        ctx = ssl.create_default_context(cafile=CA)
        with socket.create_connection((HOST, PUERTO), 5) as s:
            ctx.wrap_socket(s, server_hostname="otro-host.invalido")
        anotar("host distinto rechazado", False, "handshake aceptado")
    except ssl.SSLCertVerificationError as exc:
        anotar("host distinto rechazado", True, exc.verify_message)
    # HU-14: sin TLS no hay puerto 1883
    try:
        socket.create_connection((ENV["BROKER_IP"], 1883), 3).close()  # IP LAN: lo que ve el ESP32 (localhost:1883 puede ser un mosquitto ajeno de Windows)
        anotar("1883 cerrado", False, "acepta conexiones")
    except OSError:
        anotar("1883 cerrado (sin TLS no hay conexión)", True)
    # nodo no puede espiar lecturas de otros
    try:
        async with cliente("FARM-02-TEST", TOKEN2) as c:
            r = await c.subscribe("farmacias/+/lecturas")
            denegado = any(getattr(x, "value", 0) >= 128 for x in r) if isinstance(r, list) else int(r) >= 128
            anotar("ACL: nodo no puede suscribirse a lecturas de todos", denegado, f"suback={r}")
    except Exception as exc:
        anotar("ACL: suscripción ajena", True, repr(exc))

    fallos = [r for r in resultados if not r[1]]
    print(f"\n{len(resultados) - len(fallos)}/{len(resultados)} comprobaciones OK")
    raise SystemExit(1 if fallos else 0)


asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)  # aiomqtt no funciona con Proactor (Windows)
