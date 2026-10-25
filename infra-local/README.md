# Entorno local ColdCare (sin nube)

PostgreSQL 16 + EMQX 5.8 (TLS 8883, sin 1883, usuario = `device_id`, ACL por tópico) + backend, en Docker.
CA propia de prueba. Permite probar el nodo ColdCare1023/IoT real, el backend y el frontend en una sola PC.

## Primera vez

```bash
cd infra-local
./scripts/init.sh 192.168.18.21        # IP de ESTA PC en la red del ESP32
docker compose --profile backend up -d --build
docker compose exec backend python -m scripts.seed_dev   # usuarios demo (contraseñas SEED_* en .env)
python scripts/crear_dispositivo.py FARM-01-CDL          # alta + credencial MQTT (secrets/)
# Instalación nueva: el admin semilla aún no aceptó la privacidad (BE-014). Inicie sesión
# una vez en el frontend o añada --aceptar-privacidad a la primera ejecución.
python scripts/crear_dispositivo.py FARM-02-TEST
```

`init.sh` genera `certs/` (CA, certificado del broker y certificados para los casos negativos de HU-09),
`.env` con secretos aleatorios, `emqx/bootstrap_users.csv` (solo la cuenta del backend) y
`emqx/api_keys.txt`. Todo ignorado por git.

### Credenciales de dispositivos (BE-002)

EMQX autentica en cadena: primero su base interna, que solo contiene `backend_service`; después, el
webhook `POST /api/dispositivos/autenticar` del backend (cabecera `X-Broker-Auth` con
`BROKER_WEBHOOK_SECRET`). La credencial de cada nodo la emite el backend y vive en la tabla `devices`,
así que **rotarla o revocarla desde la API tiene efecto en el broker**: el backend además expulsa la
sesión abierta con la API de EMQX (`EMQX_API_KEY`/`EMQX_API_SECRET`). Sin el backend en marcha ningún
nodo puede autenticarse (fallo cerrado). No crear usuarios de dispositivo en la base interna de EMQX.

En EMQX Cloud (producción) hay que configurar lo mismo: autenticador HTTP hacia
`https://<backend>/api/dispositivos/autenticar` con la cabecera `X-Broker-Auth`, y una API key para
`BROKER_API_URL`/`BROKER_API_KEY`/`BROKER_API_SECRET` del backend.

### Certificado de cliente por nodo (IOT-006)

El token es un secreto portátil: copiado de un nodo, sirve desde cualquier equipo. Para ligar la identidad
a una clave que no sale del nodo, EMQX pide certificado de cliente (`verify_peer`), lo verifica contra la
CA local y pasa su CN al webhook (`cert_common_name`). El backend exige que el CN sea el `device_id`, y la
**primera** conexión con certificado liga el dispositivo: desde entonces el token solo ya no basta (queda
en la cadena de trazabilidad como `CERTIFICADO_DISPOSITIVO_VINCULADO`). `BROKER_EXIGIR_CERTIFICADO=true`
en `.env` lo exige a todos los nodos.

```bash
# en la consola del nodo (desbloqueada, con Wi-Fi configurado): tls csr  → copiar la CSR a nodo.csr
./scripts/firmar_csr_nodo.sh ESP32_FARMACIA_01 nodo.csr   # imprime "tls certificado <base64>"
# en la consola del nodo: pegar esa línea y luego reiniciar
```

La clave se genera en el nodo y nunca se muestra; el script solo firma la CSR si su sujeto es exactamente
`CN=<device_id>`, con uso `clientAuth`. Un nodo ligado que pierde la clave (`tls borrar`, NVS borrada)
necesita una CSR nueva firmada para volver a conectarse; revocar el token sigue cortándolo aunque tenga
certificado. En EMQX Cloud solo aplica si el plan admite TLS mutuo y el autenticador HTTP envía
`${cert_common_name}`; si no, los nodos siguen autenticándose solo con el token.

| Servicio | Acceso |
|---|---|
| EMQX MQTT/TLS | `<IP_LAN>:8883` (usuario = device_id) |
| EMQX dashboard | http://localhost:18083 (admin / `EMQX_DASHBOARD_PASSWORD` de `.env`) |
| Backend | http://localhost:8000/docs |
| PostgreSQL | `localhost:5433` |

## Nodo real (firmware ColdCare1023/IoT)

```bash
WIFI_SSID="MiRed" WIFI_PASS="clave" ./scripts/flash_coldcare.sh COM6 ESP32_FARMACIA_01
```

Da de alta el dispositivo en el backend y emite su credencial, copia la CA a `data/certs/root_ca.pem` del firmware, sube LittleFS
y el firmware, y aprovisiona Wi-Fi/host/token por la consola serie (`nvs set …`). `uploadfs` borra el
buffer offline; `SOLO_APP=1` lo evita. Ver variables en la cabecera del script.

## Verificar

Con el venv del backend activo (`httpx`, `aiomqtt`):

```bash
python scripts/smoke_test.py          # HU-09/10/14: TLS, token, CA ajena, host distinto, ACL, 1883
python scripts/publicar_caso.py '{}'  # una telemetría v1.0 válida → acuse "committed"
python scripts/hu12_hu19_carga.py     # HU-12/19: 5 nodos simultáneos, API en paralelo, latencia SSE
python scripts/espiar_lecturas.py 3   # captura mensajes crudos de lecturas/eventos
docker compose logs -f emqx           # evidencia HU-10: bad_username_or_password, authorization denied
```

`capturar_serial.py` guarda el monitor serie con hora local sin reiniciar el nodo (abre el puerto con
DTR/RTS bajos). Requiere `pyserial`.

## Notas

- El ESP32 (mbedTLS 2.x) valida el host contra entradas DNS del certificado: la IP va como `DNS:` e `IP:`.
  Si cambia la IP de la PC: `./scripts/init.sh <nueva_IP> --force` y reaprovisionar el nodo.
- Certificados válidos 365 días.
- `DEVICE_REGISTRY_ESTRICTO=true` (por defecto, como en producción): solo los nodos dados de alta con
  `scripts/crear_dispositivo.py` registran lecturas (HU-43). Con `false` el backend registraría el nodo con
  su primera lectura; úsese solo para pruebas aisladas.
- Las migraciones corren en el servicio `migraciones` antes de arrancar `backend`; el contenedor del
  backend corre como usuario sin privilegios (uid 10001).
- Expiración de sesión (HU-39): `JWT_EXPIRE_MIN=1 docker compose --profile backend up -d backend`.
- Windows: `aiomqtt` no funciona con el loop Proactor; por eso el backend va en contenedor y los scripts
  usan `SelectorEventLoop`.
- Parar: `docker compose down`. Borrar datos y usuarios MQTT: `docker compose down -v`.
