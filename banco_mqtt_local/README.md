# Banco de pruebas MQTT/TLS local

Réplica mínima del entorno de producción para probar el nodo **sin depender del backend real**:
broker mosquitto con TLS en 8883, usuario = `device_id`, ACL por tópico, y un backend simulado que
persiste en SQLite con `UNIQUE(device_id, boot_id, seq_no)` y publica el acuse lógico.

Verificado el 25/09/2026 en la PC de desarrollo (mosquitto 2.1.2, paho-mqtt 2.1, Python 3.14):
13/13 lecturas del perfil `excursion` con PUBACK + acuse `committed`; modo `sin_ack` → 0/6 acuses;
token inválido → `Not authorized`.

## Preparación (una vez)

```bash
# 1. Certificados de PRUEBA (IP de la PC en la red del ESP32)
./generar_certificados.sh 192.168.1.50 certs

# 2. Usuarios del broker
"C:\Program Files\mosquitto\mosquitto_passwd.exe" -c -b pwfile backend_qa <clave_backend>
"C:\Program Files\mosquitto\mosquitto_passwd.exe" -b pwfile ESP32_FARMACIA_01 <token_nodo>
"C:\Program Files\mosquitto\mosquitto_passwd.exe" -b pwfile ESP32_DEMO_01 <token_demo>

# 3. Firmware: CA del banco en el sistema de archivos y credenciales por consola
copy certs\ca.crt <repo ColdCare1023/IoT>\data\certs\root_ca.pem
pio run -e esp32dev -t uploadfs          # (borra el buffer: solo en el banco)
#   en el monitor serie:  nvs set mqtt_host 192.168.1.50 / nvs set mqtt_token <token_nodo> / reiniciar
```

> El servicio mosquitto de Windows (si está instalado) escucha en 1883 y **no** se toca. Este banco
> usa 8883 y se detiene con Ctrl+C. Permitir el puerto 8883 en el firewall de Windows para la red local.

## Ejecución

```bash
"C:\Program Files\mosquitto\mosquitto.exe" -c mosquitto.conf -v
python simulador_backend_ack.py --host 192.168.1.50 --ca certs/ca.crt --token <clave_backend> --modo normal --db banco.sqlite
```

## Casos que habilita

| Caso | Modo / acción | Resultado esperado en el nodo (monitor serie + `estado`) | HU |
|---|---|---|---|
| Flujo normal | `--modo normal` | `pendientes` vuelve a 0; `[Red] n lectura(s) con acuse logico` | HU-07 |
| **Regla de oro** | `--modo sin_ack` | `[Tx] PUBACK si, acuse logico no ...: se conserva`; `pendientes` **crece**; nada se borra | HU-07 esc. 1 |
| Reanudación | `--modo intermitente --prob 0.5` | el FIFO avanza solo con acuse; ninguna lectura se salta | HU-07 esc. 2 |
| Acuse tardío | `--modo lento --retraso 12` | timeout a los 8 s; en el reintento se libera sin republicar si el acuse ya llegó (`duplicate` si se republicó) | HU-07, HU-11 |
| Duplicados | reiniciar el nodo con `pendientes > 0` | el simulador responde `duplicate`, sin filas duplicadas en SQLite | HU-11 esc. 3 |
| Rechazo permanente | `--modo rechazar` | registros a cuarentena (`estado` → `cuarentena=N`), el FIFO sigue | HU-07 |
| Credencial inválida | token erróneo en NVS | `Conexion rechazada ... returnCode=5` (Not authorized, MQTT 3.1.1) | HU-10 esc. 2 |
| ACL | publicar como `ESP32_DEMO_01` en `farmacias/OTRO/lecturas` | el broker descarta el mensaje (log `-v`: *denied*) | HU-10 esc. 3 |
| CA incorrecta | subir otra CA a `root_ca.pem` | `sin CONNACK: TLS rechazado`; no hay fallback a 1883 | HU-09 esc. 2, HU-14 |
| Sin broker | apagar mosquitto | backoff de drenaje; lecturas en LittleFS | HU-06, HU-08 |
