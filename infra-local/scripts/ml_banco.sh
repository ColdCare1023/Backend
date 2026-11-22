#!/usr/bin/env bash
# IA (banco): corridas de entrenamiento con excursiones físicas reales (PLAN_PRUEBAS_IA §3.2), con la
# identidad ESP32_BANCO_03 para no mezclar nada inducido en la curva de ESP32_FARMACIA_01.
# Cada escenario es una corrida de paper_corrida.py en evidencia/ml/ (CORRIDAS_DIR):
#   Pn : base BASE_MIN en rango, puerta abierta n min, cola COLA_MIN            (S-P1/3/5/10)
#   C  : base, botella de 1–2 L de agua tibia junto a la sonda CARGA_MIN min, cola (S-C)
#   N  : operación normal sin tocar nada N_HORAS h (negativos)                    (S-N)
# Antes de cada acción imprime "EN 1 MIN: ..." y en el momento "AHORA: ..." (para avisar a la operadora).
# Al final vuelve a ESP32_FARMACIA_01 salvo QUEDARSE=1.
#
# Uso (desde infra-local/):  bash scripts/ml_banco.sh P10 P5 P3 C N
set -u
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."
export CORRIDAS_DIR="$(pwd -W)/evidencia/ml"   # ruta Windows: Python no entiende /c/...
REAL=ESP32_FARMACIA_01
BANCO="${BANCO:-ESP32_BANCO_03}"
BASE_MIN="${BASE_MIN:-20}"
COLA_MIN="${COLA_MIN:-30}"
CARGA_MIN="${CARGA_MIN:-20}"
N_HORAS="${N_HORAS:-10}"
MONTAJE="${MONTAJE:-sonda DS18B20 desnuda colgada de la rejilla, al centro (como el soporte 3D de farmacia); refrigerador de cocina sin medicamentos; WinPina 4G; nodo en USB de la laptop; PE-INC-01 sin aprobación formal (desvío aceptado por la operadora)}"
PY=python
PYB=../.venv/Scripts/python.exe
PYS="$HOME/.platformio/penv/Scripts/python.exe"
log() { echo "$(date -u +%H:%M:%SZ) $*"; }
marca() { $PY scripts/paper_corrida.py marca "$1" >/dev/null; log "marca: $1"; }
psql_n() {
  docker exec coldcare-develop-postgres-1 sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -tAc \"$1\""
}
en_linea() {
  psql_n "select count(*) from (select created_at, timestamp from thermal_readings where device_id='$1' order by created_at desc limit 1) t where now() - created_at < interval '20 s' and created_at - timestamp < interval '5 s';"
}
esperar_en_linea() {
  local fin=$(( $(date +%s) + $2 ))
  while [ "$(date +%s)" -lt "$fin" ]; do [ "$(en_linea "$1")" = "1" ] && return 0; sleep 5; done
  return 1
}
estable_3min() {  # "1|min-max" si las últimas 18 lecturas (3 min) están en 2,5–7,5 °C y varían < 0,5 °C
  psql_n "select case when count(*) = 18 and min(t) >= 2.5 and max(t) <= 7.5 and max(t) - min(t) < 0.5 then 1 else 0 end || '|' || round(min(t)::numeric, 2) || '-' || round(max(t)::numeric, 2) from (select temperatura_interna t from thermal_readings where device_id='$BANCO' order by created_at desc limit 18) x;"
}
esperar_rango() {  # no se arranca una corrida fuera de rango ni en un transitorio
  local fin=$(( $(date +%s) + ${RANGO_MAX_MIN:-90} * 60 )) r
  while [ "$(date +%s)" -lt "$fin" ]; do
    r=$(estable_3min)
    [ "${r%%|*}" = "1" ] && { log "en rango y estable 3 min: ${r#*|} °C"; return 0; }
    log "esperando rango estable: últimos 3 min ${r#*|} °C"; sleep 60
  done
  return 1
}
hasta() { while [ "$(date -u +%s)" -lt "$1" ]; do sleep 1; done; }
accion() {  # $1 epoch, $2 instrucción, $3 marca
  hasta $(( $1 - 60 )); log "EN 1 MIN: $2"
  hasta "$1"; marca "$3"; log "AHORA: $2"
}
rep_siguiente() { echo $(( $(ls -d "$CORRIDAS_DIR"/"$1"_r* 2>/dev/null | wc -l) + 1 )); }
cambiar() {  # reintenta 10 min: el USB puede tardar en reaparecer
  local fin=$(( $(date +%s) + 600 ))
  until "$PYS" scripts/nodo_identidad.py "$1"; do [ "$(date +%s)" -ge "$fin" ] && return 1; sleep 30; done
}
a_epoch() {  # HH:MM local -> epoch de la próxima vez que llegue esa hora
  local e; e=$(date -d "$1" +%s); [ "$e" -le "$(date +%s)" ] && e=$(date -d "tomorrow $1" +%s); echo "$e"
}
# LIMITE=HH:MM: no programar acciones de la operadora después de esa hora (se va). N_HASTA=HH:MM: fin de la S-N.
LIMITE_E=9999999999; [ -n "${LIMITE:-}" ] && LIMITE_E=$(a_epoch "$LIMITE")

# Identidad de banco.
[ -f "secrets/$BANCO.token" ] || $PYB scripts/banco_dispositivo.py alta --device-id "$BANCO" || exit 1
esperar_en_linea "$BANCO" 15 || {
  esperar_en_linea "$REAL" 120 || { log "ERROR: $REAL no está en línea sin cola; no cambio de identidad."; exit 1; }
  cambiar "$BANCO" || exit 1
  esperar_en_linea "$BANCO" 180 || { log "ERROR: $BANCO no llegó a enviar."; exit 1; }
}
log "nodo enviando como $BANCO"

for esc in "$@"; do
  case "$esc" in
    P[0-9]*) caso="SP${esc#P}"; min=${esc#P} ;;
    C) caso=SC ;;
    N) caso=SN ;;
    *) log "escenario desconocido: $esc"; continue ;;
  esac
  esperar_rango || { log "ERROR: no volvió al rango en ${RANGO_MAX_MIN:-90} min; corto aquí."; break; }
  case "$caso" in SP*) ult=$(( BASE_MIN + min )) ;; SC) ult=$(( BASE_MIN + CARGA_MIN )) ;; *) ult=0 ;; esac
  if [ "$ult" -gt 0 ] && [ $(( $(date +%s) + ult * 60 )) -gt "$LIMITE_E" ]; then
    log "salto $caso: su última acción caería después de las $LIMITE (operadora fuera)"; continue
  fi
  rep=$(rep_siguiente "$caso")
  $PY scripts/paper_corrida.py inicio "$caso" "$rep" --device "$BANCO" --horas 12 \
    --nota "IA banco $caso: base ${BASE_MIN} min, cola ${COLA_MIN} min; ${MONTAJE}" || break
  T0=$(date -u +%s)
  log "corrida $caso r$rep: base hasta $(date -d @$(( T0 + BASE_MIN * 60 )) +%H:%M) (hora local)"
  case "$caso" in
    SP*)
      A=$(( T0 + BASE_MIN * 60 )); B=$(( A + min * 60 ))
      accion "$A" "ABRE la puerta de la refri y déjala abierta ${min} min" "puerta abierta (${min} min)"
      accion "$B" "CIERRA la puerta de la refri" "puerta cerrada"
      hasta $(( B + COLA_MIN * 60 )) ;;
    SC)
      A=$(( T0 + BASE_MIN * 60 )); B=$(( A + CARGA_MIN * 60 ))
      accion "$A" "PON las botellas de agua tibia junto a la sonda (sin tocarla) y cierra" "carga tibia dentro"
      accion "$B" "SACA las botellas de agua tibia y cierra" "carga tibia fuera"
      hasta $(( B + COLA_MIN * 60 )) ;;
    SN)
      FIN_N=$(( T0 + N_HORAS * 3600 )); [ -n "${N_HASTA:-}" ] && FIN_N=$(a_epoch "$N_HASTA")
      log "operación normal hasta $(date -d @$FIN_N +%H:%M) (hora local): no abrir la refri"
      hasta "$FIN_N" ;;
  esac
  $PY scripts/paper_corrida.py fin >/dev/null
  log "corrida $caso r$rep cerrada: máx $(psql_n "select round(max(temperatura_interna)::numeric,2) from thermal_readings where device_id='$BANCO' and timestamp > to_timestamp($T0);") °C"
done

if [ "${QUEDARSE:-0}" != "1" ]; then
  cambiar "$REAL" && esperar_en_linea "$REAL" 180 && log "nodo de vuelta como $REAL" || log "AVISO: revisar vuelta a $REAL"
fi
log "listo"
