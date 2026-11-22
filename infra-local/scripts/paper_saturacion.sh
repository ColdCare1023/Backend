#!/usr/bin/env bash
# Paper (banco): saturación del buffer, ADR-18. N = 1, sintética, con identidad de banco aparte para
# que ningún sintético entre en la curva de ESP32_FARMACIA_01.
#   1. comprueba que ESP32_FARMACIA_01 está en línea y sin cola;
#   2. da de alta ESP32_BANCO_02 (demo) y cambia el nodo a esa identidad por consola;
#   3. detiene EMQX, `buffer llenar 30000` y deja capturar SATURADO_MIN min más (lecturas reales que
#      ya no caben: pérdidas y evento buffer_saturado), con el log serie guardado;
#   4. levanta EMQX y espera el vaciado (≈ 30 000 registros a ~4,7/s ≈ 1 h 50 min);
#   5. vuelve a ESP32_FARMACIA_01 y da de baja ESP32_BANCO_02.
# Necesita el nodo en el USB de la laptop (consola serie). La laptop no puede suspenderse.
#
# Uso (desde infra-local/):  bash scripts/paper_saturacion.sh [SATURADO_MIN=5]
set -u
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."
SATURADO_MIN="${1:-5}"
REAL=ESP32_FARMACIA_01
BANCO=ESP32_BANCO_02
PY=python
PYB=../.venv/Scripts/python.exe
PYS="$HOME/.platformio/penv/Scripts/python.exe"
EMQX=coldcare-develop-emqx-1
BACKEND=coldcare-develop-backend-1
log() { echo "$(date -u +%H:%M:%SZ) $*"; }
marca() { $PY scripts/paper_corrida.py marca "$1" >/dev/null; log "marca: $1"; }
psql_n() {
  docker exec coldcare-develop-postgres-1 sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -tAc \"$1\""
}
recientes() { psql_n "select count(*) from thermal_readings where device_id='$1' and created_at > now() - interval '30 s';"; }
en_linea() {  # la última lectura llegó hace < 20 s con latencia ≤ 5 s: sin cola
  psql_n "select count(*) from (select created_at, timestamp from thermal_readings where device_id='$1' order by created_at desc limit 1) t where now() - created_at < interval '20 s' and created_at - timestamp < interval '5 s';"
}
esperar_en_linea() {  # $1 device, $2 tope en s
  local fin=$(( $(date +%s) + $2 ))
  while [ "$(date +%s)" -lt "$fin" ]; do [ "$(en_linea "$1")" = "1" ] && return 0; sleep 5; done
  return 1
}

esperar_en_linea "$REAL" 120 || { log "ERROR: $REAL no está en línea sin cola; no arranco."; exit 1; }

$PYB scripts/banco_dispositivo.py alta --device-id "$BANCO" || exit 1
cambiar() { for _ in 1 2 3; do "$PYS" scripts/nodo_identidad.py "$1" && return 0; sleep 15; done; return 1; }
cambiar "$BANCO" || exit 1
esperar_en_linea "$BANCO" 180 || { log "ERROR: $BANCO no llegó a enviar; revisar consola."; exit 1; }
log "nodo enviando como $BANCO"

$PY scripts/paper_corrida.py inicio SAT 1 --device "$BANCO" --horas 5 \
  --nota "ADR-18: llenado sintético 30000 con EMQX detenido, ${SATURADO_MIN} min saturado; $BANCO (demo); WinPina 4G; nodo en USB de la laptop"
C=$(cat evidencia/paper/.corrida_activa)
docker exec "$EMQX" emqx ctl broker metrics > "$C/emqx_metricas_antes.txt" 2>&1
sleep 60
marca "broker detenido"
docker stop "$EMQX" >/dev/null
sleep 20
marca "buffer llenar 30000"
"$PYS" scripts/serie_ensayo.py "$C/serial.log" 1800 estado "buffer llenar 30000" \
  --hasta "llenado (terminado|detenido)" --despues $(( SATURADO_MIN * 60 )) --final estado
marca "fin de la ventana saturada"
docker start "$EMQX" >/dev/null
for _ in $(seq 1 60); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' $EMQX 2>/dev/null)" = "healthy" ] && break; sleep 2
done
marca "broker arriba"
ok=0
for _ in $(seq 1 18); do [ "$(recientes "$BANCO")" -gt 0 ] && { ok=1; break; }; sleep 5; done
if [ "$ok" = "0" ]; then
  docker restart "$BACKEND" >/dev/null
  marca "backend reiniciado"
fi
INI=$(date +%s)
until [ "$(en_linea "$BANCO")" = "1" ]; do
  sleep 300
  log "vaciando: $(psql_n "select count(*) from thermal_readings where device_id='$BANCO';") guardadas de $BANCO ($(( ($(date +%s) - INI) / 60 )) min)"
  [ $(( $(date +%s) - INI )) -gt 18000 ] && { log "AVISO: 5 h sin terminar de vaciar; cierro igual."; break; }
done
marca "buffer vacio"
$PY scripts/paper_corrida.py fin >/dev/null
docker exec "$EMQX" emqx ctl broker metrics > "$C/emqx_metricas_despues.txt" 2>&1
"$PYS" scripts/serie_ensayo.py "$C/serial_fin.log" 15 estado

cambiar "$REAL" || { log "ERROR: no pude volver a $REAL: hacerlo a mano."; exit 1; }
esperar_en_linea "$REAL" 180 && log "nodo de vuelta como $REAL" || log "AVISO: $REAL aún no envía"
$PYB scripts/banco_dispositivo.py baja --device-id "$BANCO" --descripcion "fin del ensayo de saturación del paper"
$PY scripts/paper_tabla.py >/dev/null
log "saturación cerrada: $C"
sed -n 1,12p "$C/resumen.md"
