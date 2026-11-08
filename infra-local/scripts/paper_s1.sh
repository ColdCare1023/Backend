#!/usr/bin/env bash
# Paper (banco): S1, operación normal. Una corrida de HORAS (12 por defecto) sin tocar nada.
# Antes de iniciar espera (hasta 45 min) a que la sonda esté en rango (2–8 °C) y sin episodio de
# alerta abierto, para que una alerta heredada no contamine la corrida. Mientras corre, mantiene la
# laptop despierta con SetThreadExecutionState (no cambia la configuración de energía; con la tapa
# cerrada igual se suspende).
#
# Uso (desde infra-local/):  bash scripts/paper_s1.sh [HORAS=12] ["nota de montaje"]
# La nota queda en corrida.json: indicar red y alimentación reales del nodo (USB de la laptop o
# cargador de pared). Con la laptop suspendida o a batería el USB no sostiene al nodo (H10).
set -u
cd "$(dirname "$0")/.."
HORAS="${1:-12}"
MONTAJE="${2:-montaje sin especificar}"
PY=python
DEV=ESP32_FARMACIA_01
log() { echo "$(date -u +%H:%M:%SZ) $*"; }
q() { docker exec coldcare-develop-postgres-1 sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Atc \"$1\""; }

LIMITE=$(( $(date -u +%s) + 45 * 60 ))
while :; do
  T=$(q "SELECT temperatura_interna FROM thermal_readings WHERE device_id = '$DEV' AND received_at > now() - interval '30 seconds' ORDER BY timestamp DESC LIMIT 1")
  A=$(q "SELECT count(*) FROM thermal_alerts WHERE device_id = '$DEV' AND episodio_abierto = 1")
  if [ -n "$T" ] && awk "BEGIN{exit !($T >= 2.5 && $T <= 7.5)}" && [ "$A" = "0" ]; then
    log "sonda en rango ($T °C), sin alerta abierta"; break
  fi
  [ "$(date -u +%s)" -ge "$LIMITE" ] && { log "AVISO: no llegó a rango en 45 min (T=$T, alertas=$A); se inicia igual"; break; }
  sleep 30
done

$PY scripts/paper_corrida.py inicio S1 1 --horas $(( HORAS + 1 )) \
  --nota "operación normal ${HORAS} h sin intervención; puerta cerrada, sonda en el refrigerador; ${MONTAJE}"
T0=$(date -u +%s)
$PY -c "
import ctypes, time
ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
time.sleep($HORAS * 3600 + 1800)
" &
DESPIERTO=$!
log "S1 iniciada; cierre a las $(date -u -d @$(( T0 + HORAS * 3600 )) +%H:%M:%SZ)"
while [ "$(date -u +%s)" -lt $(( T0 + HORAS * 3600 )) ]; do sleep 60; done
$PY scripts/paper_corrida.py fin
$PY scripts/paper_tabla.py >/dev/null
kill "$DESPIERTO" 2>/dev/null
log "S1 cerrada"
