# HU-29: respaldo programado en Railway y simulacro de restauración

Corresponde al P0-4 de la validación final del backlog. Aquí solo se describe la configuración:
nada de esto se crea desde el código. Las evidencias que hay que archivar están al final.

## Qué hace el respaldo (código)

`python -m scripts.respaldo_postgres --destino /respaldos` sigue estos pasos:

1. Abre una transacción `REPEATABLE READ` de solo lectura y exporta su snapshot con
   `pg_export_snapshot()`.
2. Comprueba que `pg_dump` no sea de una versión mayor más antigua que el servidor. Si lo es,
   falla con un motivo claro.
3. Ejecuta `pg_dump --format=custom --snapshot=<id>` sobre ese snapshot.
4. Dentro de la misma transacción calcula la huella (filas y SHA-256 en orden de clave) de
   `thermal_readings`, `traceability_records`, `audit_logs`, `users`, `devices`, `thermal_alerts`,
   `corrective_actions`, `device_config_history` y `ai_model_versions` (9 tablas; en los eslabones
   entra también su contenido: payload, hora y `chain_seq`). Las tablas se recorren por clave, no con OFFSET.
   El volcado y la huella describen el mismo instante aunque el nodo siga publicando.
5. Escribe `coldcare_<UTC>.dump` y `coldcare_<UTC>.manifiesto.json` y deja `RESPALDO_COMPLETADO`
   en `audit_logs`. Mientras trabaja, el volcado y el manifiesto se llaman `….parcial` y solo toman
   su nombre final cuando ambos están completos. Si ya existe un respaldo con la misma marca (dos
   ejecuciones en el mismo segundo), no lo sobrescribe: esa ejecución falla con
   `… ya existe: otro respaldo usó la misma marca`.
6. Si `RESPALDO_RETENER=N` (N > 0), borra los respaldos más antiguos que los N más recientes. Solo
   lo hace después de un respaldo correcto.

Cuando falla (escenario 3), registra el log `CRITICAL RESPALDO FALLIDO`, borra los `.parcial` que
esa ejecución dejara a medias (nunca un respaldo terminado, aunque tenga la misma marca), intenta
dejar `RESPALDO_FALLIDO` en `audit_logs` y termina con código de salida 2. Railway marca esa
ejecución como fallida. Un volcado cortado, por ejemplo por falta de espacio, ya no queda en el
volumen ni cuenta para `RESPALDO_RETENER`. Si el proceso muere sin pasar por ese manejo (SIGKILL),
queda un `coldcare_<UTC>.dump.parcial`: no cuenta para `RESPALDO_RETENER` ni se confunde con un
respaldo, y se puede borrar a mano desde el volumen.

El backend (servicio de la API) también lo detecta: cada `RESPALDO_VIGILANCIA_PERIODO_SEGUNDOS`
(3600 por defecto) lee el último resultado en `audit_logs` y, si el último respaldo falló, deja en
**su** log `CRITICAL ALERTA OPERATIVA: el último respaldo programado de PostgreSQL falló…` (una vez
por fallo) y `/health/detalle` → `respaldos.estado = "fallido"`. Una vez creado el cron, defina
también `RESPALDO_MAX_ANTIGUEDAD_HORAS=26` en el servicio de la API: si en 26 h no hay ningún
`RESPALDO_COMPLETADO` (el cron no corrió), el backend deja la misma alerta crítica con
`respaldos.estado = "vencido"`. Con 0 (por defecto) esa segunda revisión está apagada, para no
alertar mientras el cron aún no existe.

La imagen del cron es `Dockerfile.respaldo` y no la de la API. Lleva Python 3.12, el lockfile y
`postgresql-client-${PG_MAJOR}` del repositorio oficial apt.postgresql.org (18 por defecto, la
versión del servidor de producción). El respaldo corre como el usuario `app` (uid 10001) con
`HOME=/tmp`. El arranque solo usa root para preparar la carpeta del volumen, que Railway monta como
root.

## 1. Respaldo nativo de Railway (escenario 1)

1. Abre el servicio **Postgres** del proyecto y ve a la pestaña **Backups**.
2. Activa **Daily** (cada 24 h, se conserva 6 días). Para cubrir más tiempo, añade **Weekly**
   (cada 7 días, se conserva 27 días) y **Monthly** (cada 30 días, se conserva 89 días). Esa
   retención la fija Railway para cada programa (documentación de Volumes → Backups; la guía de
   Postgres la redondea a «1 mes» y «3 meses») y es la que se anota como retención efectiva. Si el
   plan no muestra la pestaña **Backups**, déjalo escrito en la evidencia: el respaldo queda cubierto
   por el cron del punto 2.
3. Captura la pestaña con los programas activos y la fecha del último respaldo.

Este respaldo es una copia del volumen. Sirve para recuperar errores de datos, pero no deja una
huella verificable y desaparece si se borra el volumen. Por eso existe también el volcado lógico
diario de los puntos siguientes.

## 2. Servicio cron `respaldo-postgres`

1. Comprueba la versión del servidor con `SHOW server_version;` desde la consola del servicio
   Postgres o con `psql "$DATABASE_PUBLIC_URL"`. Anota la versión mayor. En producción es 18
   (`18.6` el 15/11/2026).
2. En el proyecto, crea un servicio con **New → GitHub Repo → ColdCare1023/Backend**, rama
   `deploy/respaldo-postgres` (no `main`; ver
   [Rama de despliegue y fin de config as code](#rama-de-despliegue-y-fin-de-config-as-code)), y
   llámalo `respaldo-postgres`.
3. En **Settings**, configura en el panel:
   - **Builder** = Dockerfile y **Dockerfile path** = `Dockerfile.respaldo`.
   - **Cron Schedule** = `0 7 * * *`. Railway evalúa la expresión en UTC, así que equivale a las
     02:00 en Perú (UTC−5). La ejecución puede variar unos minutos.
   - **Restart policy** = Never, para que un fallo quede como fallo y no se reintente en bucle.

   Son los valores de `railway.respaldo.json`. En la rama `deploy/respaldo-postgres` el
   `railway.json` de la raíz los repite, porque mientras Railway lea config as code ese archivo manda
   sobre el panel. **Settings → Config-as-code → Railway config file** ya no sirve: en un servicio
   nuevo Railway descarta `/railway.respaldo.json` sin avisar.
4. En **Variables**, define estas:

   | Variable | Valor |
   |---|---|
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (variable de referencia; usa la red privada) |
   | `PG_MAJOR` | La versión mayor del paso 1: `18` en producción. Se pasa como build-arg; si falta, la imagen usa 18 |
   | `RESPALDO_DESTINO` | `/respaldos`. Es el valor por defecto de la imagen |
   | `RESPALDO_RETENER` | `14`: conserva dos semanas de volcados en el volumen |

   **No** definas `ENVIRONMENT=production` en este servicio. Ese modo exige todos los secretos de la
   API (JWT, HMAC, CORS, correo…) y el respaldo solo usa `DATABASE_URL`. Tampoco hacen falta
   `RAILWAY_RUN_UID` ni un dominio público o un healthcheck.

   `HOME` no hace falta. El arranque corre como root y `setpriv` pasa el respaldo al usuario
   `app` sin cambiar `HOME`, así que antes el proceso heredaba `HOME=/root`. El driver de PostgreSQL
   busca ahí `~/.postgresql/postgresql.key` y `app` no puede entrar en `/root`: la primera ejecución
   en producción (15/11/2026) falló con
   `CRITICAL respaldo RESPALDO FALLIDO: [Errno 13] Permission denied: '/root/.postgresql/postgresql.key'`
   y `RESPALDO FALLIDO y no se pudo registrar en audit_logs.`, porque la conexión para registrar el
   fallo choca con lo mismo. Se resolvió a mano con la variable `HOME=/tmp` en el servicio. Ahora
   la imagen lanza el respaldo con `env HOME=/tmp`: cuando esa imagen esté desplegada en la rama
   `deploy/respaldo-postgres`, la variable sobra y se puede quitar (si se deja, no cambia nada).
5. En **Volumes → New Volume**, monta el volumen en `/respaldos`. Dale un tamaño de al menos
   15 veces el tamaño del `.dump`. Puedes estimarlo con el primer respaldo o con
   `SELECT pg_size_pretty(pg_database_size(current_database()));`.
6. En **Workspace/Project Settings → Notifications**, activa los avisos de despliegue o ejecución
   fallida. Un respaldo que termina con código 2 llega por correo al equipo (escenario 3).
7. Despliega. El servicio no queda vivo: arranca según la programación, respalda y termina. Si la
   ejecución anterior no ha terminado cuando toca la siguiente, Railway se salta la nueva.

### Rama de despliegue y fin de config as code

Railway está retirando config as code. En un servicio nuevo ya no deja elegir un archivo propio
(`/railway.respaldo.json` se descarta sin avisar), pero sigue leyendo el `/railway.json` de la raíz,
que es el de la API (`dockerfilePath: infra-local/backend/Dockerfile`, `startCommand`,
`preDeployCommand`, healthcheck). Ese archivo manda sobre el panel: el 15/11/2026, con el cron
siguiendo `main`, Railway construyó y arrancó la API en lugar del respaldo.

Por eso, mientras Railway lea `railway.json`, el cron sigue la rama `deploy/respaldo-postgres`. Esa
rama es `main` con un solo cambio: el `railway.json` de la raíz tiene el contenido de
`railway.respaldo.json`. El servicio Backend sigue en `main`.

**Mantener la rama al día.** La imagen del cron se construye desde esa rama, no desde `main`. Cada
vez que en `main` cambie algo que entra en el respaldo, hay que llevarlo a la rama:
`scripts/respaldo_postgres.py`, `src/infrastructure/respaldo/`, `Dockerfile.respaldo`, lo que el
script importa (`src/infrastructure/config.py`, `src/infrastructure/database/`) o `requirements.txt`.

```bash
git fetch origin
git switch deploy/respaldo-postgres
git pull --ff-only
git merge --no-edit origin/main
git diff --stat origin/main   # solo debe aparecer railway.json
git push origin deploy/respaldo-postgres
```

Si `main` cambió el `railway.json` de la API, el merge choca en ese archivo. Conserva la versión de
la rama: `git checkout --ours railway.json && git add railway.json && git commit --no-edit`. Cada
push a la rama redespliega el servicio cron.

**Corte del 2026-12-01.** Desde esa fecha Railway deja de leer los archivos de config as code y solo
cuenta lo que esté en el panel de cada servicio.

- **Cron `respaldo-postgres`**: el panel ya tiene Builder = Dockerfile, Dockerfile path =
  `Dockerfile.respaldo` y Cron Schedule = `0 7 * * *` (paso 3). Comprueba también Restart policy =
  Never. Después del corte el cron puede volver a seguir `main` y la rama se puede borrar, una vez
  que una ejecución desde `main` construya `Dockerfile.respaldo` y deje `Respaldo completado`.
- **Servicio Backend**: hoy también toma su configuración del `railway.json` de la raíz. **Antes del
  2026-12-01** hay que pasar esos valores a su panel (**Settings**), o dejarán de aplicarse:

  | Ajuste del panel | Valor actual en `railway.json` |
  |---|---|
  | Builder y Dockerfile path | Dockerfile, `infra-local/backend/Dockerfile` |
  | Custom Start Command | `python -m scripts.arrancar_api` (vacío también sirve: es el `CMD` de ese Dockerfile) |
  | Pre-deploy Command | `alembic upgrade head` |
  | Healthcheck path y timeout | `/health`, 120 s |
  | Replicas | 1 |
  | Restart policy | On failure, con 5 reintentos como máximo |
  | Serverless (apagado por inactividad) | Desactivado (`sleepApplication: false`) |

  `docs/operacion/HU-55_despliegue_railway.md` explica para qué sirve cada uno.

### Comprobación del escenario 1

- Logs de la ejecución: `Respaldo completado: coldcare_<UTC>.dump (<bytes> bytes)`.
- Fila `RESPALDO_COMPLETADO` en Auditoría, con el SHA-256 y el número de filas de las 9 tablas.
- Contenido del volumen (con el servicio vinculado mediante `railway link` y `railway service`):
  `railway volume files list /`.

### Escenario 3 (fallo provocado)

El criterio pide la alerta crítica **en el log del backend**. El vigilante de la API solo lee
`audit_logs`, así que el cron tiene que poder escribir allí `RESPALDO_FALLIDO`. Por eso el fallo se
provoca con la base **accesible**: un `pg_dump` de una versión mayor anterior a la del servidor.

Antes de empezar, define en el servicio de la API `RESPALDO_MAX_ANTIGUEDAD_HORAS=26` y deja
`RESPALDO_VIGILANCIA_PERIODO_SEGUNDOS` en 3600 (por defecto) o en un valor menor para esperar menos.

1. En el cron, cambia de forma temporal `PG_MAJOR` a una versión **anterior** a la del servidor
   (paso 2.1; en producción, `17`, porque el servidor es 18) y redespliega. La imagen se construye
   con `postgresql-client-17`. Una versión posterior no sirve: `pg_dump` sí vuelca servidores más
   antiguos y el respaldo saldría bien.
2. Adelanta la programación unos minutos. La granularidad mínima es de 5 minutos. Hasta el
   2026-12-01 la programación se cambia en el `railway.json` de la rama `deploy/respaldo-postgres`,
   que manda sobre el panel; después, en el panel.
3. En la ejecución, `verificar_cliente_compatible` rechaza el cliente antes de volcar. Captura:
   - el log del cron:
     `<hora> CRITICAL respaldo RESPALDO FALLIDO: pg_dump 17 es anterior al servidor PostgreSQL 18: construya Dockerfile.respaldo con PG_MAJOR=18`;
   - el aviso de Railway de ejecución fallida (código de salida 2);
   - en Auditoría, la fila `RESPALDO_FALLIDO` con resultado «fallido».
4. En el log del servicio **Backend**, como mucho un `RESPALDO_VIGILANCIA_PERIODO_SEGUNDOS` después
   (3600 s por defecto), aparece una sola vez el mensaje crítico del logger `interface.tareas`
   `ALERTA OPERATIVA: el último respaldo programado de PostgreSQL falló (<hora>) y no hay uno correcto posterior. Revise el servicio cron de respaldo (HU-29).`
   Captura esa línea y, con un token de administrador, `/health/detalle` →
   `respaldos.estado = "fallido"`.
5. Restaura `PG_MAJOR=18` y `0 7 * * *` y redespliega. Tras la siguiente ejecución correcta
   (`RESPALDO_COMPLETADO`), la siguiente pasada del vigilante deja `respaldos.estado = null`.

Por qué no se usa una contraseña errónea en `DATABASE_URL`: con la base inaccesible el cron no puede
escribir `RESPALDO_FALLIDO` (deja `RESPALDO FALLIDO y no se pudo registrar en audit_logs.`), así que
la alerta solo queda en el log del cron y en el aviso de Railway. El vigilante de la API no ve
ningún fallo nuevo. Con `RESPALDO_MAX_ANTIGUEDAD_HORAS=26` lo detecta recién cuando pasan 26 h sin
`RESPALDO_COMPLETADO`, como `respaldos.estado = "vencido"`; con 0 (por defecto) no lo detecta nunca.
La prueba `test_un_fallo_del_cron_llega_al_log_del_backend` reproduce este escenario con la base
accesible.

## 3. Simulacro de restauración (escenario 2)

Hay que hacerlo una vez por sprint y antes de cada entrega, siempre contra una base **aislada**.
`verificar_restauracion` se niega a restaurar sobre `DATABASE_URL`.

1. Descarga el último respaldo del volumen:
   ```bash
   railway volume files list /
   railway volume files download /coldcare_<UTC>.dump ./simulacro/coldcare_<UTC>.dump
   railway volume files download /coldcare_<UTC>.manifiesto.json ./simulacro/coldcare_<UTC>.manifiesto.json
   ```
2. Levanta una base vacía con la misma versión mayor. Puede ser local
   (`docker run --rm -e POSTGRES_PASSWORD=simulacro -p 55432:5432 postgres:18`) o un segundo
   servicio Postgres temporal en Railway, que se borra al terminar.
3. Verifica la restauración. En la máquina hacen falta `pg_restore` de la misma versión mayor y el
   entorno del backend (`requirements.txt`):
   ```bash
   RESTAURACION_DATABASE_URL=postgresql://postgres:simulacro@localhost:55432/postgres \
   python -m scripts.verificar_restauracion --manifiesto simulacro/coldcare_<UTC>.manifiesto.json
   ```
4. El resultado se escribe en `simulacro/coldcare_<UTC>.restauracion.json`. Debe tener
   `archivo_integro: true` y `coincide: true`, con `coinciden_filas` y `coincide_huella` en las 9
   tablas (8 en un respaldo anterior a este cambio, que declara sus columnas). El código de salida es 0. Si algo no coincide, sale con 2 y un log crítico.
5. Archiva en `docs/evidencias/HU-29/` el `.restauracion.json`, el manifiesto (sin el `.dump`, que
   contiene datos personales) y las capturas de los escenarios 1 y 3.

Variante sin volumen, por ejemplo antes de crear el cron:
`DATABASE_URL=<DATABASE_PUBLIC_URL> python -m scripts.respaldo_postgres --destino simulacro`
genera un respaldo local con el mismo código. Después se siguen los pasos 2 a 5.

## Pruebas automatizadas que lo respaldan

- `tests/integration/test_hu29_snapshot_postgres.py`: usa PostgreSQL, `pg_dump` y `pg_restore`
  reales (pgserver o `TEST_POSTGRES_URL`). Inserta filas entre el volcado y la huella y comprueba
  que la restauración da `coincide=true`. Con el código anterior daba `coincide=false`. También
  ejecuta el cron (`main`) contra esa base: un fallo de almacenamiento deja `RESPALDO_FALLIDO` y el
  vigilante de la API escribe la alerta crítica (`test_un_fallo_del_cron_llega_al_log_del_backend`),
  y un `.dump` cortado se borra y no cuenta para la retención.
- `tests/integration/test_hu29_respaldo_restauracion.py`: comprueba la huella, las 9 tablas, el contenido de los eslabones, la
  paginación por clave, los manifiestos antiguos y el fallo crítico.
- `tests/unit/test_hu29_servicio_cron_respaldo.py`: comprueba `Dockerfile.respaldo` (cliente 18,
  usuario `app` con `HOME=/tmp`), `railway.respaldo.json`, la retención y que esta guía mantenga la
  retención de Railway, el fallo provocado con la base accesible y la rama de despliegue con el
  corte del 2026-12-01.

## Estado en producción (15/11/2026)

Hecho: servicio cron `respaldo-postgres` con su volumen, simulacro de restauración y fallo
provocado. Los backups nativos no están disponibles en el plan Trial. La evidencia está en
`docs/evidencias/HU-29/2026-11-15_cron_simulacro_y_fallo.md`.

## Pendiente (no se puede hacer desde el código)

- Confirmar la primera ejecución programada (16/11/2026, 02:00 en Lima).
- Copiar a `docs/evidencias/HU-29/` el `.restauracion.json` y el manifiesto originales del volumen.
- Activar los backups nativos si se pasa a un plan que los incluya (paso 1).
