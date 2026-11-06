#!/usr/bin/env bash
# Paper (banco): S9, puerta abierta. N repeticiones con horario fijo: la operadora separa los imanes
# del MC-38 a la hora APERTURA_k y los junta 5 min después; el script pone las marcas en el mismo
# segundo (reloj de la laptop y del celular sincronizados por red).
#
# Uso (desde infra-local/):  bash scripts/paper_s9.sh <primera apertura UTC, ej. 2026-11-06T03:45:00Z> [N=5] [PERIODO_MIN=8] [ABIERTA_MIN=5]
set -u
cd "$(dirname "$0")/.."
T0=$(date -u -d "$1" +%s)
N="${2:-5}"
PERIODO=$(( ${3:-8} * 60 ))
ABIERTA=$(( ${4:-5} * 60 ))
PY=python
log() { echo "$(date -u +%H:%M:%SZ) $*"; }
hasta() { while [ "$(date -u +%s)" -lt "$1" ]; do sleep 0.2; done; }
marca() { $PY scripts/paper_corrida.py marca "$1" >/dev/null; log "marca: $1"; }

for r in $(seq 1 "$N"); do
  AP=$(( T0 + (r - 1) * PERIODO ))
  hasta $(( AP - 60 ))
  $PY scripts/paper_corrida.py inicio S9 "$r" --horas 1 \
    --nota "imanes del MC-38 separados ${4:-5} min con la puerta del refrigerador cerrada; WinPina 4G"
  hasta "$AP"
  marca "puerta abierta"
  hasta $(( AP + ABIERTA ))
  marca "puerta cerrada"
  hasta $(( AP + ABIERTA + 60 ))
  $PY scripts/paper_corrida.py fin >/dev/null
  log "S9 r$r cerrada"
done
$PY scripts/paper_tabla.py >/dev/null
log "listo"
