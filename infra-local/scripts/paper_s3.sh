#!/usr/bin/env bash
# Paper (banco): S3, corte largo. N = 1. En WinPina el hotspot no se puede apagar sin dejar sin red
# a la laptop (va por Ethernet al mismo router), así que el corte se hace deteniendo EMQX HORAS h con
# el nodo y el backend vivos: para el nodo es "broker inalcanzable" (como S7, pero largo). Mide la
# ocupación máxima del buffer, el vaciado y los mensajes descartados del broker. Se declara así en
# el paper (ver hallazgos.md).
#
# Uso (desde infra-local/):  bash scripts/paper_s3.sh [HORAS=6] ["montaje"]
set -u
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."
HORAS="${1:-6}"
MONTAJE="${2:-nodo en USB de la laptop (enchufada, sin suspender)}"
DEV=ESP32_FARMACIA_01
PY=python
EMQX=coldcare-develop-emqx-1
BACKEND=coldcare-develop-backend-1
log() { echo "$(date -u +%H:%M:%SZ) $*"; }
marca() { $PY scripts/paper_corrida.py marca "$1" >/dev/null; log "marca: $1"; }
psql_n() {
  docker exec coldcare-develop-postgres-1 sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -tAc \"$1\""
}
recientes() { psql_n "select count(*) from thermal_readings where device_id='$DEV' and created_at > now() - interval '30 s';"; }

# Previo: el nodo tiene que estar enviando en línea (y la cola vacía).
[ "$(recientes)" -gt 0 ] || { log "ERROR: $DEV no envió en los últimos 30 s; no arranco."; exit 1; }

$PY scripts/paper_corrida.py inicio S3 1 --horas $(( HORAS + 2 )) \
  --nota "EMQX detenido ${HORAS} h (corte largo; nodo en WinPina 4G, broker en $(grep '^BROKER_IP=' .env | cut -d= -f2)); ${MONTAJE}"
C=$(cat evidencia/paper/.corrida_activa)
docker exec "$EMQX" emqx ctl broker metrics > "$C/emqx_metricas_antes.txt" 2>&1
sleep 120
marca "broker detenido"
docker stop "$EMQX" >/dev/null
FIN=$(( $(date +%s) + HORAS * 3600 ))
while [ "$(date +%s)" -lt "$FIN" ]; do
  sleep 600
  [ "$(docker inspect -f '{{.State.Running}}' $EMQX)" = "true" ] && log "AVISO: EMQX está corriendo antes de tiempo"
  log "corte en curso: faltan $(( (FIN - $(date +%s)) / 60 )) min"
done
docker start "$EMQX" >/dev/null
for _ in $(seq 1 60); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' $EMQX 2>/dev/null)" = "healthy" ] && break; sleep 2
done
marca "broker arriba"
ok=0
for _ in $(seq 1 18); do [ "$(recientes)" -gt 0 ] && { ok=1; break; }; sleep 5; done
if [ "$ok" = "0" ]; then
  docker restart "$BACKEND" >/dev/null
  marca "backend reiniciado"
fi
$PY scripts/paper_corrida.py fin >/dev/null   # espera el vaciado (tope 20 min)
docker exec "$EMQX" emqx ctl broker metrics > "$C/emqx_metricas_despues.txt" 2>&1
$PY scripts/paper_tabla.py >/dev/null
log "S3 cerrada: $C"
sed -n 1,12p "$C/resumen.md"
