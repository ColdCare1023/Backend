# Prueba H: preparar EMQX Cloud Serverless para el banco (HU-44 y HU-10)

Para cerrar el DoD de HU-44 y los escenarios 2 y 3 de HU-10, las pruebas A a G
se repiten contra un deployment Serverless real. Esta guía dice qué se prepara
antes de la sesión de banco, qué se le entrega a quien opera el nodo y qué no
se entrega nunca.

La base, las migraciones y el frontend siguen siendo los de `infra-local`; solo
el backend cambia de broker, con un archivo de sobrescritura y un `.env` aparte.

## 1. Deployment

1. Crear el deployment Serverless en la consola de EMQX Cloud.
2. En **Overview**, anotar:
   - **Address** (host). El puerto MQTT con TLS es **8883**; Serverless no
     expone puerto sin TLS.
   - La dirección de la **REST API** y crear una **API key** del deployment
     (App ID y App Secret). El secreto se muestra una sola vez.

## 2. Certificado de la CA

Serverless exige TLS y la consola ofrece la CA en **Overview → CA Certificate**
(es el mismo `emqxsl-ca.crt` que publica EMQX en
`https://assets.emqx.com/data/emqxsl-ca.crt`).

- **Backend:** guardarlo como `infra-local/certs/emqxsl-ca.crt` (la carpeta
  `certs/` no se versiona).
- **Firmware:** el nodo valida la CA que `scripts/embeber_ca.py` embebe desde
  `IoT/data/certs/root_ca.pem` (no versionado). Para el banco Serverless:
  1. Guardar la CA local actual: `cp data/certs/root_ca.pem data/certs/root_ca_local.pem`.
  2. Copiar `emqxsl-ca.crt` como `data/certs/root_ca.pem`.
  3. Compilar y flashear `esp32dev` (`pio run -e esp32dev -t upload`). En el
     log de arranque debe verse la CA embebida y la conexión TLS al host nuevo.
  4. En el nodo: `nvs set mqtt_host <Address>`.
  5. Al volver a la pila local, restaurar `root_ca_local.pem` y repetir 3 y 4
     con la IP de la laptop.

  Con otra CA el nodo no conecta: el error TLS es lo primero que hay que
  descartar si no aparece el CONNACK.

## 3. Autenticación y reglas ACL

**Access Control → Client Authentication** (base integrada):

| Usuario | Contraseña |
|---|---|
| `backend_service` | la de `SERVERLESS_MQTT_PASSWORD` |
| `<device_id>` del nodo | el token que emite el backend al dar de alta o rotar (sección 5) |

**Access Control → Client Authorization.** Serverless no tiene el selector de
modo lista blanca: se reproduce con una regla final (documentación de EMQX
Cloud, «Authorization Mode»).

1. Generar las reglas exactas desde la base local:
   `python -m scripts.reglas_acl_emqx --device <device_id> --formato csv`.
2. Cargar las reglas por **Username** (backend y nodo). EMQX evalúa primero las
   reglas por usuario o client ID y después las de **All Users**.
3. En **All Users**, agregar `#`, *Publish & Subscribe*, **Deny**. Es la
   denegación por defecto: todo lo que no esté permitido arriba se rechaza.

El desafío de posesión lo publica el backend con un cliente efímero
(`<MQTT_CLIENT_ID>-pop-<aleatorio>`) y el usuario `backend_service`: lo cubre la
regla de publicación de `farmacias/+/ack`.

Más detalle y la verificación de HU-10: `ADR-ACL_EMQX_Serverless.md`.

## 4. Backend del banco contra Serverless

```bash
cd Backend/infra-local
cp serverless.env.example serverless.env   # completar; no se versiona
docker compose --env-file .env --env-file serverless.env \
  -f docker-compose.yml -f docker-compose.serverless.yml \
  --profile backend up -d --build backend
```

- `docker-compose.serverless.yml` solo cambia el broker del servicio `backend`
  (host, usuario, CA, `BROKER_USUARIOS_MODO=manual` y la API para expulsar
  sesiones). El `.env` de la pila local no se toca.
- Usar un `SERVERLESS_MQTT_CLIENT_ID` propio: si el backend de Railway apunta
  al mismo deployment, dos clientes con el mismo ID se expulsan entre sí.
- Comprobar en el log del backend la conexión al host de Serverless y, en la
  consola (**Clients**), el cliente del backend conectado.

Para volver a la pila local: el mismo comando sin `--env-file serverless.env`
ni `-f docker-compose.serverless.yml`.

## 5. Qué se entrega a quien opera el nodo

| Se entrega | Cómo |
|---|---|
| Host (Address) y puerto 8883 | Por el canal del equipo |
| `emqxsl-ca.crt` | Es pública: puede ir por el repositorio de evidencias o por chat |
| Acceso a la consola, o acuerdo de quién edita | En B', E y F hay que cambiar o borrar la contraseña del usuario del nodo |
| `serverless.env` | En persona o por un gestor de contraseñas; nunca por chat |

**El token del nodo no se entrega a nadie.** Lo emite el backend al dar de alta
o rotar (se muestra una sola vez) y se carga en el nodo por la consola serie
con `nvs set mqtt_token` o `nvs set mqtt_relevo`. El monitor serie no hace eco
del valor.

## 6. Lista para empezar la prueba H

- [ ] Deployment en marcha; host y API key anotados.
- [ ] `emqxsl-ca.crt` en `infra-local/certs/` y firmware compilado con esa CA.
- [ ] `backend_service` y el usuario del nodo creados en la consola.
- [ ] Reglas por usuario cargadas y regla final `#` Deny en All Users.
- [ ] Backend del banco arriba con `serverless.env`; cliente visible en la consola.
- [ ] Nodo con `mqtt_host` del deployment y Wi-Fi con internet.
- [ ] Prueba G primero (token inválido y ACL): confirma autenticación y reglas
      antes de las pruebas largas.
