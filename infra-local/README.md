# Entorno local ColdCare (sin nube)

PostgreSQL 16 + EMQX 5.8 con TLS en 8883 + backend, en Docker. CA propia de prueba.

## Primera vez

```bash
cd infra-local
./scripts/init.sh 192.168.18.21        # IP de ESTA PC en la red del ESP32
docker compose --profile backend up -d --build
docker compose exec backend python -m scripts.seed_dev   # usuarios demo
```

`init.sh` genera `certs/` (CA, certificado del broker y certificados para los casos negativos de HU-09),
`.env` con secretos aleatorios y los tokens MQTT. Todo ignorado por git.

| Servicio | Acceso |
|---|---|
| EMQX MQTT/TLS | `<IP_LAN>:8883` |
| EMQX dashboard | http://localhost:18083 (admin / `EMQX_DASHBOARD_PASSWORD` de `.env`) |
| Backend | http://localhost:8000/docs |
| PostgreSQL | `localhost:5433` |

## Notas

- El ESP32 (mbedTLS 2.x) valida el host contra entradas DNS del certificado: la IP va como `DNS:` e `IP:`.
  Si cambia la IP de la PC: `./scripts/init.sh <nueva_IP> --force`.
- El backend confía en la CA privada con `MQTT_CA_FILE`; sin ella usa el almacén del sistema (EMQX Cloud).
- Certificados válidos 365 días.
- Parar: `docker compose down`. Borrar datos: `docker compose down -v`.
