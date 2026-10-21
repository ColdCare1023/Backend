#!/usr/bin/env bash
# IOT-006: firma la CSR de un nodo con la CA local (certs/ca.key) y devuelve el
# certificado en base64 de una línea, listo para la consola del nodo:
#
#   1. En el nodo (consola desbloqueada):  tls csr      → copiar la CSR a nodo.csr
#   2. Aquí:  ./scripts/firmar_csr_nodo.sh <device_id> nodo.csr
#   3. En el nodo:  tls certificado <línea impresa>  y  reiniciar
#
# La clave privada nunca sale del nodo: aquí solo pasa la CSR (pública).
set -euo pipefail
cd "$(dirname "$0")/.."

if [ $# -ne 2 ]; then
  echo "Uso: $0 <device_id> <archivo.csr>" >&2
  exit 2
fi
DEVICE_ID="$1"
CSR="$2"
DIAS="${DIAS_CERTIFICADO_NODO:-365}"

if ! [[ "$DEVICE_ID" =~ ^[A-Za-z0-9_-]{1,64}$ ]]; then
  echo "device_id invalido: $DEVICE_ID" >&2
  exit 2
fi
for f in certs/ca.crt certs/ca.key "$CSR"; do
  [ -f "$f" ] || { echo "Falta $f (ejecutar antes scripts/init.sh)" >&2; exit 1; }
done

# La firma de la CSR prueba que quien la generó tiene la clave privada.
openssl req -in "$CSR" -noout -verify >/dev/null 2>&1 || { echo "CSR con firma invalida" >&2; exit 1; }
SUJETO="$(openssl req -in "$CSR" -noout -subject -nameopt RFC2253 | sed 's/^subject=//')"
if [ "$SUJETO" != "CN=${DEVICE_ID}" ]; then
  echo "La CSR es de '${SUJETO}', no de CN=${DEVICE_ID}: no se firma." >&2
  exit 1
fi

EXT="$(mktemp)"
CRT="$(mktemp)"
trap 'rm -f "$EXT" "$CRT"' EXIT
printf 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=clientAuth\n' > "$EXT"
# Solo entran el sujeto ya comprobado y estas extensiones (las de la CSR se ignoran).
openssl x509 -req -in "$CSR" -CA certs/ca.crt -CAkey certs/ca.key -CAcreateserial -CAserial certs/ca_nodos.srl \
  -days "$DIAS" -sha256 -extfile "$EXT" -out "$CRT" 2>/dev/null
openssl verify -CAfile certs/ca.crt -purpose sslclient "$CRT" >/dev/null

echo "Certificado de ${DEVICE_ID} (${DIAS} dias). En la consola del nodo:" >&2
printf 'tls certificado %s\n' "$(openssl x509 -in "$CRT" -outform DER | base64 | tr -d '\n')"
