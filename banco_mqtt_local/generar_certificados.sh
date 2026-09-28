#!/usr/bin/env bash
# Genera una CA de PRUEBA y el certificado del broker local para el banco de
# pruebas del nodo (TLS en 8883, igual que EMQX; el firmware no admite 1883).
#
# Uso (Git Bash):   ./generar_certificados.sh 192.168.1.50 [carpeta_salida]
#   192.168.1.50 = IP de la PC en la red Wi-Fi a la que se conecta el ESP32.
#
# El ESP32 (mbedTLS) compara el host contra entradas DNS del certificado, no
# contra entradas IP: por eso la IP va dos veces en el SAN (DNS: e IP:).
# Estas claves son SOLO para el banco. Nunca se usan en EMQX ni en el local.
set -euo pipefail
IP="${1:?Uso: $0 <IP_de_la_PC> [carpeta_salida]}"
OUT="${2:-certs}"
mkdir -p "$OUT"
cd "$OUT"
export MSYS_NO_PATHCONV=1   # Git Bash: no convertir "/CN=..." en ruta de Windows

# Configuración mínima propia: algunas instalaciones (p. ej. el ODBC de
# PostgreSQL) dejan OPENSSL_CONF apuntando a un archivo inexistente.
printf '[req]\ndistinguished_name = dn\n[dn]\n' > openssl_min.cnf
export OPENSSL_CONF=openssl_min.cnf   # relativa: vale en Git Bash, Linux y macOS

# La CA debe declararse CA (basicConstraints/keyUsage): mbedTLS en el ESP32 y
# Python 3.13+ (verificación X.509 estricta) rechazan una CA sin ellas.
openssl req -x509 -newkey rsa:2048 -nodes -days 365 -keyout ca.key -out ca.crt \
  -subj "/CN=ThermoTrace banco de pruebas CA" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "subjectKeyIdentifier=hash" >/dev/null 2>&1

openssl req -newkey rsa:2048 -nodes -keyout servidor.key -out servidor.csr -subj "/CN=${IP}" >/dev/null 2>&1

{
  echo "subjectAltName=DNS:${IP},IP:${IP},DNS:localhost,IP:127.0.0.1"
  echo "extendedKeyUsage=serverAuth"
  echo "basicConstraints=CA:FALSE"
  echo "keyUsage=critical,digitalSignature,keyEncipherment"
  echo "subjectKeyIdentifier=hash"
  echo "authorityKeyIdentifier=keyid,issuer"
} > ext.cnf
openssl x509 -req -in servidor.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 365 \
  -out servidor.crt -extfile ext.cnf >/dev/null 2>&1

openssl verify -CAfile ca.crt servidor.crt >/dev/null
rm -f servidor.csr ext.cnf ca.srl openssl_min.cnf
echo "Listo en $(pwd): ca.crt (va a data/certs/root_ca.pem del firmware), servidor.crt, servidor.key"
