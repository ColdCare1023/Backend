#!/usr/bin/env bash
# Paper (banco): S7, broker caído. N repeticiones sin operador: EMQX detenido MIN minutos con el
# nodo y el backend vivos; al volver, el nodo vacía su buffer. Guarda las métricas de EMQX antes
# y después (mensajes descartados) y, en la primera repetición, el log serie del nodo (H1).
#
# Uso (desde infra-local/):  bash scripts/paper_s7.sh [N=5] [MIN=30]
set -u
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."
N="${1:-5}"
MIN="${2:-30}"
PY=python
EMQX=coldcare-develop-emqx-1
BACKEND=coldcare-develop-backend-1
PYSERIE="$HOME/.platformio/penv/Scripts/python.exe"
log() { echo "$(date -u +%H:%M:%SZ) $*"; }
marca() { $PY scripts/paper_corrida.py marca "$1" >/dev/null; log "marca: $1"; }
activa() { cat evidencia/paper/.corrida_activa; }
recientes() {  # lecturas del nodo confirmadas en los últimos 30 s
  docker exec coldcare-develop-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "select count(*) from thermal_readings where device_id='"'"'ESP32_FARMACIA_01'"'"' and created_at > now() - interval '"'"'30 s'"'"';"'
}

for r in $(seq 1 "$N"); do
  $PY scripts/paper_corrida.py inicio S7 "$r" --horas 2 \
    --nota "EMQX detenido ${MIN} min; nodo y backend vivos; WinPina 4G (192.168.18.114)"
  C=$(activa)
  SERIE_PID=""
  if [ "$r" = "1" ] && [ -x "$PYSERIE" ]; then
    "$PYSERIE" scripts/capturar_serial.py "$C/serial.log" $(( (MIN + 8) * 60 )) COM6 &
    SERIE_PID=$!
  fi
  docker exec "$EMQX" emqx ctl broker metrics > "$C/emqx_metricas_antes.txt" 2>&1
  sleep 60
  marca "broker detenido"
  docker stop "$EMQX" >/dev/null
  sleep $(( MIN * 60 ))
  docker start "$EMQX" >/dev/null
  for _ in $(seq 1 60); do
    [ "$(docker inspect -f '{{.State.Health.Status}}' $EMQX 2>/dev/null)" = "healthy" ] && break; sleep 2
  done
  marca "broker arriba"
  # El backend se reconecta solo; si en 90 s no entra ninguna lectura, se reinicia y se anota.
  ok=0
  for _ in $(seq 1 18); do [ "$(recientes)" -gt 0 ] && { ok=1; break; }; sleep 5; done
  if [ "$ok" = "0" ]; then
    docker restart "$BACKEND" >/dev/null
    marca "backend reiniciado"
  fi
  $PY scripts/paper_corrida.py fin >/dev/null
  docker exec "$EMQX" emqx ctl broker metrics > "$C/emqx_metricas_despues.txt" 2>&1
  [ -n "$SERIE_PID" ] && kill "$SERIE_PID" 2>/dev/null
  $PY scripts/paper_tabla.py >/dev/null
  log "S7 r$r cerrada"
done
log "listo"
