"""Envía comandos de consola y guarda el log serie con hora local, ocultando lo que parezca secreto.

    python scripts/serie_ensayo.py <log> <segundos> [comando ...] [--hasta REGEX] [--despues S] [--final CMD]

- Envía los comandos al abrir (1,5 s entre uno y otro).
- Termina a los <segundos>, o S segundos después de la primera línea que cumpla --hasta.
- --final CMD se envía 12 s antes de terminar (p. ej. "estado").
- Imprime en pantalla solo las líneas [BANCO], las de pérdida o saturación y las respuestas a estado.
"""
import os
import re
import sys
import time

import serial
from serial.tools import list_ports

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OCULTAR = re.compile(r"(?i)pass|token|clave|secret|psk|BEGIN")
VER = re.compile(r"\[BANCO\]|perdid|saturad|NO almacenada|buffer:|device_id=|wifi=|buffer_80")

argv, opciones = [], {}
it = iter(sys.argv[1:])
for a in it:
    if a in ("--hasta", "--despues", "--final"):
        opciones[a] = next(it)
    else:
        argv.append(a)
if len(argv) < 2:
    raise SystemExit(__doc__)
log, segundos, comandos = argv[0], float(argv[1]), argv[2:]
hasta = re.compile(opciones["--hasta"]) if "--hasta" in opciones else None
despues = float(opciones.get("--despues", 0))
final = opciones.get("--final")

puerto = os.environ.get("NODO_PUERTO") or next(
    (p.device for p in list_ports.comports() if "BTHENUM" not in (p.hwid or "") and p.vid is not None), None)
if not puerto:
    raise SystemExit("No encuentro el ESP32 por USB (define NODO_PUERTO).")
s = serial.Serial(None, 115200, timeout=0.5)
s.port, s.dtr, s.rts = puerto, False, False  # sin DTR/RTS el nodo no se reinicia al abrir
s.open()
fin = time.time() + segundos
final_enviado = False
with open(log, "a", encoding="utf-8") as f:
    for c in comandos:
        s.write((c + "\n").encode())
        f.write(time.strftime("%H:%M:%S ") + f">>> {c}\n")
        time.sleep(1.5)
    while time.time() < fin:
        if final and not final_enviado and time.time() > fin - 12:
            s.write((final + "\n").encode())
            f.write(time.strftime("%H:%M:%S ") + f">>> {final}\n")
            final_enviado = True
        crudo = s.readline()
        if not crudo:
            continue
        t = crudo.decode("utf-8", errors="replace").rstrip()
        if not t.strip():
            continue
        t = "[oculto]" if OCULTAR.search(t) else t
        f.write(time.strftime("%H:%M:%S ") + t + "\n")
        f.flush()
        if VER.search(t):
            print(time.strftime("%H:%M:%S ") + t, flush=True)
        if hasta and hasta.search(t):
            hasta = None
            fin = min(fin, time.time() + despues)
s.close()
