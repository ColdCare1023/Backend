#!/usr/bin/env bash
# Paper (banco): S6 (caída del backend antes del COMMIT) y línea base sin acuse de aplicación,
# N repeticiones de cada uno, sin operador. Usa la inyección de fallas de prueba del backend
# (MQTT_PRUEBA_FALLO_ANTES_COMMIT, MQT-01) contra la MISMA base de evidencia.
#
# Uso (desde infra-local/):  bash scripts/paper_s6_base.sh [N=5] [solo: s6|base]
#
# S6:   'salir_una_vez'. Se borra la marca → la próxima lectura mata el backend (os._exit) antes
#       del COMMIT → 20 s caído → se levanta → el nodo reenvía lo que no tuvo acuse.
# BASE: 'excepcion'. Durante 120 s cada COMMIT falla con el backend vivo (recibe y no confirma)
#       → se vuelve al backend normal → el nodo reenvía.
# Al terminar deja el backend NORMAL (sin el archivo de pruebas).
set -u
# Git Bash convierte rutas como /pruebas en C:/Program Files/Git/pruebas: desactivarlo.
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."
N="${1:-5}"
SOLO="${2:-}"
PY=python
BACKEND=coldcare-develop-backend-1
DB=$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2-)
log() { echo "$(date -u +%H:%M:%SZ) $*"; }
marca() { $PY scripts/paper_corrida.py marca "$1" >/dev/null; log "marca: $1"; }
esperar_salud() { for _ in $(seq 1 60); do curl -sf -o /dev/null http://127.0.0.1:8000/health && return 0; sleep 2; done; log "AVISO: backend sin salud"; return 1; }
backend_falla() {
  POSTGRES_DB_PRUEBAS="$DB" MQTT_PRUEBA_FALLO_ANTES_COMMIT="$1" docker compose -p coldcare-develop \
    -f docker-compose.yml -f docker-compose.pruebas.yml --profile backend up -d backend >/dev/null 2>&1
  esperar_salud
  # La marca de 'una_vez' vive en un volumen de root; el backend corre como usuario 10001.
  docker exec -u 0 "$BACKEND" chmod 1777 /pruebas >/dev/null 2>&1
}
backend_normal() {
  docker compose -p coldcare-develop -f docker-compose.yml --profile backend up -d backend >/dev/null 2>&1
  esperar_salud
}

if [ "$SOLO" != "base" ]; then
  backend_falla salir_una_vez
  # Si la marca no existe, la falla está armada: dispararla fuera de las corridas.
  docker exec "$BACKEND" sh -c 'touch /pruebas/fallo_una_vez' 2>/dev/null
  for r in $(seq 1 "$N"); do
    $PY scripts/paper_corrida.py inicio S6 "$r" --horas 1 \
      --nota "salir_una_vez: backend muere antes del COMMIT, 20 s caído; misma base; WinPina 4G (192.168.18.114)"
    sleep 25
    marca "falla armada"
    docker exec "$BACKEND" rm -f /pruebas/fallo_una_vez
    for _ in $(seq 1 60); do [ "$(docker inspect -f '{{.State.Running}}' $BACKEND)" = "false" ] && break; sleep 1; done
    if [ "$(docker inspect -f '{{.State.Running}}' $BACKEND)" = "false" ]; then
      marca "backend caido"
    else
      log "AVISO: el backend no cayó en 60 s (repetición inválida)"
    fi
    sleep 20
    docker start "$BACKEND" >/dev/null
    esperar_salud
    marca "backend arriba"
    sleep 30
    $PY scripts/paper_corrida.py fin >/dev/null
    log "S6 r$r cerrada"
  done
fi

if [ "$SOLO" != "s6" ]; then
  for r in $(seq 1 "$N"); do
    backend_normal
    $PY scripts/paper_corrida.py inicio BASE "$r" --horas 1 \
      --nota "excepcion: 120 s con cada COMMIT fallando y el backend vivo; misma base; WinPina 4G (192.168.18.114)"
    sleep 25
    backend_falla excepcion
    marca "falla on"
    sleep 120
    marca "falla off"
    backend_normal
    sleep 30
    $PY scripts/paper_corrida.py fin >/dev/null
    log "BASE r$r cerrada"
  done
fi

backend_normal
$PY scripts/paper_tabla.py
log "listo"
