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

EMQX autentica con su **autenticación integrada** (usuario = `device_id`, contraseña = token), como
describen OE2.I1, OE1.I2 y el criterio de HU-10. La base contiene `backend_service` (bootstrap) y los
dispositivos, que **crea, reemplaza y elimina el backend** con la API de administración de EMQX
(`EMQX_API_KEY`/`EMQX_API_SECRET`): emitir o rotar con `POST /api/dispositivos/{id}/credenciales/rotar`
aprovisiona el token nuevo y expulsa la sesión anterior; revocar o dar de baja elimina el usuario del
broker y expulsa la sesión. La respuesta de la rotación informa `credencial_aprovisionada` (`null` si el
backend no tiene la API del broker configurada: en ese caso el token no sirve hasta aprovisionarlo). No
crear usuarios de dispositivo a mano en EMQX: el backend no podría revocarlos.

En EMQX Cloud (producción) hay que activar la misma autenticación integrada y generar una API key para
`BROKER_API_URL`/`BROKER_API_KEY`/`BROKER_API_SECRET` del backend. Falta confirmar que el plan Serverless
exponga la API de administración de usuarios (ver limitaciones de OE2.I1).

### Certificado de cliente por nodo (IOT-006, opcional)

El token es un secreto portátil: copiado de un nodo, sirve desde cualquier equipo. EMQX local pide
certificado de cliente (`verify_peer`) y lo verifica contra la CA local, pero no es obligatorio y el
backend ya no contrasta el CN con el `device_id`: la identidad del nodo la fijan usuario + token. Exigirlo
solo es posible fuera de EMQX Serverless (que no admite certificados de cliente X.509), configurando el
listener y la autorización del broker.

```bash
# en la consola del nodo (desbloqueada, con Wi-Fi configurado): tls csr  → copiar la CSR a nodo.csr
./scripts/firmar_csr_nodo.sh ESP32_FARMACIA_01 nodo.csr   # imprime "tls certificado <base64>"
# en la consola del nodo: pegar esa línea y luego reiniciar
```

La clave se genera en el nodo y nunca se muestra; el script solo firma la CSR si su sujeto es exactamente
`CN=<device_id>`, con uso `clientAuth`.

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
