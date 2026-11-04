#!/usr/bin/env bash
# Flashea el firmware ColdCare1023/IoT y lo aprovisiona contra el stack local
# por la consola serie (nvs set ...). El token lo emite el backend (BE-002) y se
# guarda en secrets/; la wifi sale de variables de entorno: no se escriben en
# ningún repositorio ni se imprimen. Requiere el backend en marcha.
#
# Uso (Git Bash, desde infra-local/):
#   WIFI_SSID="MiRed" WIFI_PASS="clave" ./scripts/flash_coldcare.sh [COM6] [ESP32_FARMACIA_01] [entorno_pio]
#
# Variables opcionales:
#   FW_DIR      repo del firmware          (defecto: ../../IoT, clon hermano de este repo)
#   PIO         ejecutable de PlatformIO   (defecto: pio)
#   PY_SERIAL   python con pyserial        (defecto: python)
#   PY_BACKEND  python con httpx           (defecto: ../.venv/Scripts/python.exe o python)
#   SOLO_APP=1  no reescribe LittleFS (uploadfs BORRA el buffer offline del nodo)
#   SOLO_NVS=1  solo reaprovisiona, sin compilar ni flashear
#   ACEPTAR_PRIVACIDAD=1  instalación nueva: usted es el administrador y acepta la
#               política de privacidad (BE-014); sin esto el alta responde 403
set -euo pipefail
cd "$(dirname "$0")/.."
PUERTO="${1:-COM6}"; DEV="${2:-ESP32_FARMACIA_01}"; ENTORNO="${3:-esp32dev}"
: "${WIFI_SSID:?define WIFI_SSID}"; : "${WIFI_PASS:?define WIFI_PASS}"
FW="${FW_DIR:-../../IoT}"
PIO="${PIO:-pio}"
PY_SERIAL="${PY_SERIAL:-python}"
if [[ -z "${PY_BACKEND:-}" ]]; then
  PY_BACKEND=python; [[ -x ../.venv/Scripts/python.exe ]] && PY_BACKEND=../.venv/Scripts/python.exe
fi

# La plataforma del firmware (pioarduino, ESP-IDF 5.5) instala el toolchain con
# idf_tools.py, que aborta con «MSys/Mingw is not supported» si ve la variable
# MSYSTEM. Git Bash la vuelve a poner en todo programa de Windows aunque se haga
# `env -u MSYSTEM`, así que la primera compilación lanzada desde aquí fallaba.
# Python sí puede arrancar PlatformIO con un entorno sin ella.
ejecutar_pio() {
  if [[ -n "${MSYSTEM:-}" ]]; then
    "$PY_SERIAL" -c 'import os, subprocess, sys
entorno = {k: v for k, v in os.environ.items() if k != "MSYSTEM"}
sys.exit(subprocess.call(sys.argv[1:], env=entorno))' "$PIO" "$@"
  else
    "$PIO" "$@"
  fi
}

if [[ -n "${ACEPTAR_PRIVACIDAD:-}" ]]; then
  "$PY_BACKEND" scripts/crear_dispositivo.py "$DEV" --aceptar-privacidad
else
  "$PY_BACKEND" scripts/crear_dispositivo.py "$DEV"
fi

FORZAR_ID=0
if [[ -z "${SOLO_NVS:-}" ]]; then
  cp certs/ca.crt "$FW/data/certs/root_ca.pem"   # ignorado por git en el repo del firmware
  if [[ -z "${SOLO_APP:-}" ]]; then
    ejecutar_pio run -d "$FW" -e "$ENTORNO" -t uploadfs --upload-port "$PUERTO"
    # uploadfs acaba de vaciar el buffer: lo que quede pendiente al aprovisionar son
    # lecturas de ESTE nodo desde que arrancó, que pueden salir con el device_id nuevo.
    FORZAR_ID=1
  fi
  ejecutar_pio run -d "$FW" -e "$ENTORNO" -t upload --upload-port "$PUERTO"
fi

BROKER_IP=$(grep '^BROKER_IP=' .env | cut -d= -f2 | tr -d '\r') \
TOKEN_FILE="secrets/${DEV}.token" DEV="$DEV" PUERTO="$PUERTO" FORZAR_ID="$FORZAR_ID" \
PYTHONIOENCODING=utf-8 "$PY_SERIAL" - <<'PY'
import os, re, sys, time, serial


def enviar(linea: str) -> None:
    s.write((linea + "\n").encode()); s.flush()


def respuesta(clave: str, espera: float = 4.0) -> str:
    """Respuesta de la consola a `nvs set <clave>`, entre las líneas de log del nodo.
    La consola no hace eco ni imprime valores: solo `-> OK`/`RECHAZADO` y el motivo."""
    patron = re.compile(rf"nvs set {re.escape(clave)} (?:->|RECHAZADO)[^\n]*|Consola bloqueada[^\n]*")
    texto, fin = "", time.monotonic() + espera
    while time.monotonic() < fin:
        texto += s.read(s.in_waiting or 1).decode("utf-8", "replace")
        m = patron.search(texto)
        if m and "\n" in texto[m.end():]:
            break
    m = patron.search(texto)
    return m.group(0).strip() if m else ""


s = serial.Serial(None, 115200, timeout=0.2)
s.port, s.dtr, s.rts = os.environ["PUERTO"], False, False
s.open()
time.sleep(4)  # dejar arrancar el nodo tras el flasheo
s.reset_input_buffer()
token = open(os.environ["TOKEN_FILE"]).read().strip()
dev = os.environ["DEV"]
# device_id primero: si no se puede fijar, no se escribe nada más.
cmds = [("device_id", dev), ("wifi_ssid", os.environ["WIFI_SSID"]), ("wifi_pass", os.environ["WIFI_PASS"]),
        ("mqtt_host", os.environ["BROKER_IP"]), ("mqtt_token", token)]
for clave, valor in cmds:
    enviar(f"nvs set {clave} {valor}")
    resp = respuesta(clave)
    if clave == "device_id" and "pendientes" in resp and os.environ["FORZAR_ID"] == "1":
        enviar(f"nvs set device_id {valor} forzar")
        resp = respuesta(clave)
    if "-> OK" not in resp:
        print(f"  nvs {clave}: FALLÓ ({resp or 'la consola no respondió'})", file=sys.stderr)
        print("Aprovisionamiento incompleto: el nodo NO se reinició (el broker lo rechazaría con una "
              "identidad o un token que no corresponden). Revise el monitor serie.", file=sys.stderr)
        sys.exit(1)
    print(f"  nvs {clave}: ok")
enviar("reiniciar")
print("Nodo aprovisionado y reiniciado.")
PY
