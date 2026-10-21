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

"$PY_BACKEND" scripts/crear_dispositivo.py "$DEV"

if [[ -z "${SOLO_NVS:-}" ]]; then
  cp certs/ca.crt "$FW/data/certs/root_ca.pem"   # ignorado por git en el repo del firmware
  [[ -n "${SOLO_APP:-}" ]] || "$PIO" run -d "$FW" -e "$ENTORNO" -t uploadfs --upload-port "$PUERTO"
  "$PIO" run -d "$FW" -e "$ENTORNO" -t upload --upload-port "$PUERTO"
fi

BROKER_IP=$(grep '^BROKER_IP=' .env | cut -d= -f2) \
TOKEN_FILE="secrets/${DEV}.token" DEV="$DEV" PUERTO="$PUERTO" \
PYTHONIOENCODING=utf-8 "$PY_SERIAL" - <<'PY'
import os, time, serial
s = serial.Serial(None, 115200, timeout=0.5)
s.port, s.dtr, s.rts = os.environ["PUERTO"], False, False
s.open()
time.sleep(4)  # dejar arrancar el nodo tras el flasheo
s.reset_input_buffer()
token = open(os.environ["TOKEN_FILE"]).read().strip()
cmds = [("wifi_ssid", os.environ["WIFI_SSID"]), ("wifi_pass", os.environ["WIFI_PASS"]),
        ("mqtt_host", os.environ["BROKER_IP"]), ("mqtt_token", token), ("device_id", os.environ["DEV"])]
for clave, valor in cmds:
    s.write(f"nvs set {clave} {valor}\n".encode()); s.flush(); time.sleep(0.6)
    resp = s.read(s.in_waiting or 1).decode("utf-8", "replace")
    ok = "valor no mostrado" in resp or f"nvs set {clave}" in resp
    print(f"  nvs {clave}: {'ok' if ok else 'sin confirmación'}")
s.write(b"reiniciar\n"); s.flush()
print("Nodo aprovisionado y reiniciado.")
PY
