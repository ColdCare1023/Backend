#!/usr/bin/env bash
# Prepara el entorno local una sola vez: CA de prueba + certificado del broker,
# secretos aleatorios (.env) y usuarios MQTT de EMQX (bootstrap_users.csv).
#
# Uso (Git Bash):  ./scripts/init.sh 192.168.18.21
#   IP = dirección de la PC en la red Wi-Fi/LAN a la que se conecta el ESP32.
# Con --force se regeneran certificados y secretos (invalida lo ya flasheado).
#
# Estas claves son SOLO para desarrollo local. Nada de esto se usa en la nube.
set -euo pipefail
cd "$(dirname "$0")/.."
IP="${1:?Uso: $0 <IP_LAN_de_la_PC> [--force]}"
FORCE="${2:-}"
export MSYS_NO_PATHCONV=1

if [[ -f .env && "$FORCE" != "--force" ]]; then
  echo "Ya existe .env. Usa --force para regenerar (invalida certificados y tokens actuales)."
  exit 1
fi

rand() { openssl rand -hex "${1:-16}"; }
mkdir -p certs secrets

# --- CA y certificado del broker -------------------------------------------
# El ESP32 (mbedTLS 2.x) compara el host contra entradas DNS del certificado,
# no contra IP: por eso la IP va como DNS: e IP:. `emqx` es el nombre con el
# que se conecta el backend dentro de Docker.
printf '[req]\ndistinguished_name = dn\n[dn]\n' > certs/openssl_min.cnf

( cd certs
  export OPENSSL_CONF=openssl_min.cnf   # relativa: vale en Git Bash y Linux
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 -keyout ca.key -out ca.crt \
    -subj "/CN=ThermoTrace local CA" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "subjectKeyIdentifier=hash" >/dev/null 2>&1
  openssl req -newkey rsa:2048 -nodes -keyout servidor.key -out servidor.csr -subj "/CN=${IP}" >/dev/null 2>&1
  {
    echo "subjectAltName=DNS:${IP},IP:${IP},DNS:localhost,IP:127.0.0.1,DNS:emqx"
    echo "extendedKeyUsage=serverAuth"
    echo "basicConstraints=CA:FALSE"
    echo "keyUsage=critical,digitalSignature,keyEncipherment"
    echo "subjectKeyIdentifier=hash"
    echo "authorityKeyIdentifier=keyid,issuer"
  } > ext.cnf
  openssl x509 -req -in servidor.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 365 \
    -out servidor.crt -extfile ext.cnf >/dev/null 2>&1
  openssl verify -CAfile ca.crt servidor.crt >/dev/null

  # Casos negativos de HU-09: una CA ajena y un certificado con host distinto.
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 -keyout otra_ca.key -out otra_ca.crt \
    -subj "/CN=CA ajena (negativa)" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" >/dev/null 2>&1
  openssl req -newkey rsa:2048 -nodes -keyout host_distinto.key -out host_distinto.csr -subj "/CN=otro-host.invalido" >/dev/null 2>&1
  { echo "subjectAltName=DNS:otro-host.invalido"; echo "extendedKeyUsage=serverAuth"; echo "basicConstraints=CA:FALSE"; } > ext2.cnf
  openssl x509 -req -in host_distinto.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 365 \
    -out host_distinto.crt -extfile ext2.cnf >/dev/null 2>&1
  rm -f *.csr *.srl ext.cnf ext2.cnf openssl_min.cnf
)

# --- Secretos y usuarios MQTT ----------------------------------------------
PG_PASS=$(rand 16); JWT=$(rand 32); EMQX_DASH=$(rand 12)
MQTT_BACKEND=$(rand 16); TOKEN_NODO=$(rand 16); TOKEN_OTRO=$(rand 16)

cat > .env <<ENV
POSTGRES_USER=farmacia
POSTGRES_PASSWORD=${PG_PASS}
POSTGRES_DB=farmacia_db
JWT_SECRET_KEY=${JWT}
EMQX_DASHBOARD_USER=admin
EMQX_DASHBOARD_PASSWORD=${EMQX_DASH}
MQTT_BACKEND_PASSWORD=${MQTT_BACKEND}
DEVICE_REGISTRY_ESTRICTO=false
BROKER_IP=${IP}
ENV

# usuario,clave,es_superusuario  (EMQX los hashea al importar)
cat > emqx/bootstrap_users.csv <<CSV
user_id,password,is_superuser
backend_service,${MQTT_BACKEND},false
FARM-01-CDL,${TOKEN_NODO},false
FARM-02-TEST,${TOKEN_OTRO},false
CSV

# Credenciales del nodo para aprovisionar el ESP32 (no se imprimen en pantalla).
printf '%s' "$TOKEN_NODO" > secrets/FARM-01-CDL.token
printf '%s' "$TOKEN_OTRO" > secrets/FARM-02-TEST.token

echo "Listo."
echo "  Certificados : infra-local/certs  (ca.crt -> data/certs/root_ca.pem del firmware)"
echo "  Variables    : infra-local/.env"
echo "  Tokens nodo  : infra-local/secrets/*.token"
echo "Siguiente: docker compose up -d"
