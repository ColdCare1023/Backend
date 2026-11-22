"""Cambia la identidad y el broker del nodo por la consola serie, sin reflashear.

    python scripts/nodo_identidad.py <device_id> [mqtt_host] [--forzar]

- Escribe `device_id`, `mqtt_host` (por defecto BROKER_IP de .env) y `mqtt_token`, que se lee de
  secrets/<device_id>.token. El token no se imprime y la consola del nodo no lo repite.
- Antes pide `estado`: si el device_id cambia y hay lecturas pendientes, se niega (ADR-18: se
  enviarían con la identidad nueva), salvo con --forzar.
- La CA del broker tiene que estar ya en LittleFS (el bundle actual trae la CA local y DigiCert G2).
- Usa el python de PlatformIO (pyserial). Puerto: NODO_PUERTO o el primer USB que no sea Bluetooth.
"""
import os
import re
import sys
import time
from pathlib import Path

import serial
from serial.tools import list_ports

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
RAIZ = Path(__file__).resolve().parent.parent
OCULTAR = re.compile(r"(?i)pass|token|clave|secret|psk|BEGIN")

args = [a for a in sys.argv[1:] if not a.startswith("--")]
FORZAR = "--forzar" in sys.argv
if not args:
    raise SystemExit(__doc__)
DEV = args[0]
HOST = args[1] if len(args) > 1 else next(
    (ln.split("=", 1)[1].strip() for ln in (RAIZ / ".env").read_text().splitlines() if ln.startswith("BROKER_IP=")), "")
TOKEN_FILE = RAIZ / "secrets" / f"{DEV}.token"
if not HOST or not TOKEN_FILE.exists():
    raise SystemExit(f"Falta el host o {TOKEN_FILE.relative_to(RAIZ)}.")

puerto = os.environ.get("NODO_PUERTO") or next(
    (p.device for p in list_ports.comports() if "BTHENUM" not in (p.hwid or "") and p.vid is not None), None)
if not puerto:
    raise SystemExit("No encuentro el ESP32 por USB (define NODO_PUERTO).")

s = serial.Serial(None, 115200, timeout=0.2)
s.port, s.dtr, s.rts = puerto, False, False
s.open()


def leer(segundos: float, hasta: re.Pattern | None = None) -> str:
    texto, fin = "", time.monotonic() + segundos
    while time.monotonic() < fin:
        texto += s.read(s.in_waiting or 1).decode("utf-8", "replace")
        if hasta and (m := hasta.search(texto)) and "\n" in texto[m.end():]:
            break
    return texto


def enviar(linea: str) -> None:
    s.write((linea + "\n").encode())
    s.flush()


try:
    time.sleep(0.3)
    s.reset_input_buffer()
    enviar("estado")
    # La consola puede tardar hasta ~10 s si el nodo está bloqueado intentando conectar.
    texto = leer(12, re.compile(r"perdidas: total=\d+"))
    actual = re.search(r"device_id=(\S+)", texto)
    pend = re.search(r"pendientes=(\d+)/(\d+)", texto)
    for linea in texto.splitlines():
        if re.search(r"device_id=|wifi=|buffer:|perdidas:", linea) and not OCULTAR.search(linea):
            print("  " + linea.strip())
    if not actual or not pend:
        raise SystemExit("El nodo no respondió a 'estado': no cambié nada. Repite en unos segundos.")
    if actual.group(1) != DEV and int(pend.group(1)) > 0 and not FORZAR:
        raise SystemExit(f"Hay {pend.group(1)} lecturas pendientes de {actual.group(1)}: vaciar antes de "
                         f"cambiar a {DEV} (o --forzar). No cambié nada.")
    token = TOKEN_FILE.read_text().strip()
    for clave, valor in (("device_id", DEV), ("mqtt_host", HOST), ("mqtt_token", token)):
        if clave == "device_id" and FORZAR and actual.group(1) != DEV:
            valor += " forzar"
        enviar(f"nvs set {clave} {valor}")
        resp = re.search(rf"nvs set {clave} (?:->|RECHAZADO)[^\n]*|Consola bloqueada[^\n]*",
                         leer(12, re.compile(rf"nvs set {clave} (?:->|RECHAZADO)|Consola bloqueada")))
        resp_txt = resp.group(0).strip() if resp else "la consola no respondió"
        if "-> OK" not in resp_txt:
            raise SystemExit(f"nvs {clave}: FALLÓ ({resp_txt.replace(token, '***')}). No se reinició el nodo.")
        print(f"  nvs {clave}: ok")
    del token
    enviar("reiniciar")
    print(f"Nodo con device_id={DEV} y mqtt_host={HOST}; reiniciando.")
finally:
    s.close()
