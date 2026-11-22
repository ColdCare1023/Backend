#!/usr/bin/env bash
# Paper (banco): error de la sonda en el punto de hielo + latencia recepción → alerta crítica.
# N repeticiones con horario fijo. La sonda DS18B20 (desnuda) descansa en el refrigerador (2–8 °C);
# a la hora INMERSION_k la operadora la pasa al baño de hielo (hielo picado + agua, agitado) y a los
# EN_HIELO_MIN la devuelve al refrigerador. El script pone las marcas en el mismo segundo y vigila
# thermal_alerts durante toda la corrida (paper_vigia_alertas.py).
#
# Uso (desde infra-local/):  bash scripts/paper_hielo.sh <primera inmersión UTC> [N=5] [PERIODO_MIN=12] [EN_HIELO_MIN=6]
# La red y la alimentación van en MONTAJE (variable de entorno); se copian a la nota de cada corrida.
set -u
cd "$(dirname "$0")/.."
T0=$(date -u -d "$1" +%s)
N="${2:-5}"
PERIODO=$(( ${3:-12} * 60 ))
EN_HIELO=$(( ${4:-6} * 60 ))
PY=python
MONTAJE="${MONTAJE:-WinPina 4G; nodo en USB de la laptop}"
DEVICE="${DEVICE:-ESP32_FARMACIA_01}"   # identidad que envía (p. ej. ESP32_BANCO_03)
REP0="${REP0:-0}"                       # numeración: la primera repetición es REP0+1
log() { echo "$(date -u +%H:%M:%SZ) $*"; }
hasta() { while [ "$(date -u +%s)" -lt "$1" ]; do sleep 0.2; done; }
marca() { $PY scripts/paper_corrida.py marca "$1" >/dev/null; log "marca: $1"; }

for r in $(seq 1 "$N"); do
  IN=$(( T0 + (r - 1) * PERIODO ))
  FIN=$(( IN + PERIODO - 60 ))
  hasta $(( IN - 60 ))
  $PY scripts/paper_corrida.py inicio HIELO "$(( REP0 + r ))" --horas 1 --device "$DEVICE" \
    --nota "sonda DS18B20 desnuda: refrigerador → baño de hielo ${4:-6} min → refrigerador; ${MONTAJE}"
  CARPETA=$(cat evidencia/paper/.corrida_activa)
  $PY scripts/paper_vigia_alertas.py "$CARPETA" $(( FIN - $(date -u +%s) - 5 )) --device "$DEVICE" &
  VIGIA=$!
  hasta "$IN"
  marca "sonda en hielo"
  hasta $(( IN + EN_HIELO ))
  marca "sonda al refrigerador"
  wait "$VIGIA"
  hasta "$FIN"
  $PY scripts/paper_corrida.py fin >/dev/null
  log "HIELO r$(( REP0 + r )) cerrada"
done
$PY scripts/paper_tabla.py >/dev/null
log "listo"
