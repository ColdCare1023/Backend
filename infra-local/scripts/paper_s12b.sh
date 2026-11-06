#!/usr/bin/env bash
# Paper (banco): S12b, nodo sin energía. N repeticiones con horario fijo: la operadora desenchufa el nodo
# a la hora T_k y lo vuelve a enchufar 3 min después; el script pone las marcas en el mismo
# segundo (reloj de la laptop y del celular sincronizados por red).
#
# Uso (desde infra-local/):  bash scripts/paper_s12b.sh <primer desenchufe UTC> [N=5] [PERIODO_MIN=7] [SIN_ENERGIA_MIN=3] [ESPERA_MIN=2]
set -u
cd "$(dirname "$0")/.."
T0=$(date -u -d "$1" +%s)
N="${2:-5}"
PERIODO=$(( ${3:-7} * 60 ))
ABIERTA=$(( ${4:-3} * 60 ))
ESPERA=$(( ${5:-2} * 60 ))
PY=python
log() { echo "$(date -u +%H:%M:%SZ) $*"; }
hasta() { while [ "$(date -u +%s)" -lt "$1" ]; do sleep 0.2; done; }
marca() { $PY scripts/paper_corrida.py marca "$1" >/dev/null; log "marca: $1"; }

for r in $(seq 1 "$N"); do
  AP=$(( T0 + (r - 1) * PERIODO ))
  hasta $(( AP - 60 ))
  $PY scripts/paper_corrida.py inicio S12b "$r" --horas 1 \
    --nota "nodo desenchufado ${4:-3} min (cargador 5 V de pared); WinPina 4G"
  hasta "$AP"
  marca "nodo desenchufado"
  hasta $(( AP + ABIERTA ))
  marca "nodo enchufado"
  hasta $(( AP + ABIERTA + ESPERA ))
  $PY scripts/paper_corrida.py fin >/dev/null
  log "S12b r$r cerrada"
done
$PY scripts/paper_tabla.py >/dev/null
log "listo"
