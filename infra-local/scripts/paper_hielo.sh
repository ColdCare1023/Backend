#!/usr/bin/env bash
# Paper (banco): error de la sonda en el punto de hielo + latencia recepción → alerta crítica.
# N repeticiones con horario fijo. La sonda DS18B20 (desnuda) descansa en el refrigerador (2–8 °C);
# a la hora INMERSION_k la operadora la pasa al baño de hielo (hielo picado + agua, agitado) y a los
# EN_HIELO_MIN la devuelve al refrigerador. El script pone las marcas en el mismo segundo y vigila
# thermal_alerts durante toda la corrida (paper_vigia_alertas.py).
#
# Uso (desde infra-local/):  bash scripts/paper_hielo.sh <primera inmersión UTC> [N=5] [PERIODO_MIN=12] [EN_HIELO_MIN=6]
set -u
cd "$(dirname "$0")/.."
T0=$(date -u -d "$1" +%s)
N="${2:-5}"
PERIODO=$(( ${3:-12} * 60 ))
EN_HIELO=$(( ${4:-6} * 60 ))
PY=python
log() { echo "$(date -u +%H:%M:%SZ) $*"; }
hasta() { while [ "$(date -u +%s)" -lt "$1" ]; do sleep 0.2; done; }
marca() { $PY scripts/paper_corrida.py marca "$1" >/dev/null; log "marca: $1"; }

for r in $(seq 1 "$N"); do
  IN=$(( T0 + (r - 1) * PERIODO ))
  FIN=$(( IN + PERIODO - 60 ))
  hasta $(( IN - 60 ))
  $PY scripts/paper_corrida.py inicio HIELO "$r" --horas 1 \
    --nota "sonda DS18B20 desnuda: refrigerador → baño de hielo ${4:-6} min → refrigerador; WinPina 4G; nodo en cargador de pared"
  CARPETA=$(cat evidencia/paper/.corrida_activa)
  $PY scripts/paper_vigia_alertas.py "$CARPETA" $(( FIN - $(date -u +%s) - 5 )) &
  VIGIA=$!
  hasta "$IN"
  marca "sonda en hielo"
  hasta $(( IN + EN_HIELO ))
  marca "sonda al refrigerador"
  wait "$VIGIA"
  hasta "$FIN"
  $PY scripts/paper_corrida.py fin >/dev/null
  log "HIELO r$r cerrada"
done
$PY scripts/paper_tabla.py >/dev/null
log "listo"
