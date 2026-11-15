# 11 — Datos de prueba y corridas: publicador P-13 y exportador P-1

Cierra dos prerrequisitos del plan de pruebas de la IA preventiva (`auditoria/PLAN_PRUEBAS_IA.md`,
sección 2.1):

| ID | Herramienta | Ruta | Pruebas |
|---|---|---|---|
| P-13 | Publicador MQTT de prueba para el stack local, sin `simulated` | `infra-local/scripts/publicador_prueba_mqtt.py` | `tests/unit/test_p13_publicador_prueba_mqtt.py` |
| P-1 | Exportador de corridas a CSV con `corrida_id` | `scripts/exportar_corridas.py` | `tests/integration/test_p1_exportar_corridas.py` |

El publicador solo publica en el broker del stack local, con un dispositivo de prueba. El exportador
solo lee: abre la base en modo de solo lectura y nunca escribe.

## 1. Publicador MQTT de prueba (P-13)

### Para qué

Algunos casos necesitan que una lectura con un valor y una hora exactos pase por el modelo preventivo:
D01, D08, D09, D12, D18 y D19 en local, y la verificación de la regla en la sección 6.
`simulacion_excursiones/simular_excursion.py` siempre marca `simulated=true`, y el backend no predice
esas lecturas (`uc/predecir_riesgo_preventivo.py`). `scripts/simulador_esp32.py` crea lecturas
manuales. El publicador envía telemetría del contrato v1.0 como lo haría un nodo y **nunca** incluye
`simulated`. Si un CSV trae esa columna, se rechaza.

### Guarda de seguridad

Se comprueba antes de leer el token o abrir una conexión, también con `--dry-run`. Si falla, el script
sale con código 2 y no publica nada.

- **Broker:** `localhost`, `127.0.0.1` o `::1`. Otro host exige `--permitir-host` y además tiene que
  ser de red local: IP privada o de enlace local, un nombre sin dominio (`emqx` dentro de Compose) o
  un nombre `.local`, `.lan`, `.localhost`, `.test`, `.internal` o `.home.arpa`. Los dominios de
  EMQX Cloud (`*.emqxsl.com`, `*.emqx.cloud`, …) y de Railway, incluida su red privada
  (`*.railway.internal`, donde está la base de producción), se rechazan siempre.
- **Identidad:** el `device_id` tiene que ser de prueba, es decir, llevar `TEST` o `PRUEBA` como
  segmento (`PRUEBA_P13_01`, que es el valor por defecto, o `FARM-02-TEST`). Las identidades de
  producción o del banco físico (`ESP32_FARMACIA*`, `ESP32_DEMO*`, `ESP32_BANCO*` y cualquier id con
  `FARMACIA`) se rechazan incluso contra `localhost`, porque un puerto local puede ser un túnel hacia
  producción. Además, P-13 no debe reutilizar la identidad del nodo de banco: sus `boot_id`/`seq_no`
  chocarían con los del ESP32.
- **Credencial:** se usa la misma sesión que el nodo: usuario y client_id = `device_id`, contraseña =
  token, TLS 8883 validando la CA local y QoS 1. El token se lee de `infra-local/secrets/<device_id>.token`
  y no se imprime.

### Preparación (una vez, en el stack local)

```bash
cd infra-local
python scripts/crear_dispositivo.py PRUEBA_P13_01     # alta SOLO en el backend local + token en secrets/
```

### Escenarios

Todos aceptan las opciones comunes: `--intervalo-s` (10, como el banco), `--inicio`, `--hueco-s`,
`--uptime-inicial`, `--time-quality`, `--sin-sht31`, `--sin-mc38`, `--boot-id`,
`--registro evidencia.csv` y `--dry-run`.

| Escenario | Qué publica | Casos del plan |
|---|---|---|
| `en_rango --lecturas N --temp T` | N lecturas a T °C | Calentamiento (≥ 90 lecturas = 15 min a 10 s), D01 y D18 paso 4 (con `--hueco-s`) |
| `valores --valores 1.9,2.0,8.0,8.1` | Valores exactos (`null` = falla del DS18B20) | D09 límites, D08 (receta abajo), D01 paso 3 y D18 paso 5 (`8.5`) |
| `rampa --lecturas N --desde A --hasta B` | Rampa lineal con ambos extremos | Anticipación, D03–D06 en local |
| `excursion --base --subida --pico --evento --bajada --cola` | Base en `--temp`, subida a `--pico` (8,5 por defecto), meseta, bajada y cola | D10, D18 paso 1 (con `--cola 0`) |
| `puerta_abierta --base --evento --pico --cola` | Puerta abierta en subida y evento: `door_open_s` es lo abierto en el ciclo y `door_open_streak_s` la racha | Variante `termico_puerta`, D15 |
| `falla_sensor --base --evento --cola [--motivo crc_error]` | DS18B20 en `SENSOR_FAULT` durante el evento | D11 en local |
| `csv --archivo escenario.csv` | Una fila por lectura, con hora exacta (`captured_at`) o desplazamiento (`offset_s`) | Secuencias propias, D12 (`time_quality=unsynced`), D19 |

Horas:

- Sin `--inicio`, en **tiempo real**: la lectura i se captura en t0 + i·intervalo y se envía en ese
  instante, como el nodo, aunque el acuse de la anterior no haya llegado. El backend acusa después del
  COMMIT, que va detrás de la inferencia (hasta `IA_INFERENCIA_TIMEOUT_S`), y el nodo tampoco espera
  ese acuse para la captura siguiente. Es lo que hay que usar en D03, D07 y D10, donde cuentan la
  latencia y el correo, para que la alerta no salga marcada como diferida (`historica`), y en D08.
- `--hueco-s N` retrasa la primera lectura N segundos sin conectarse ni publicar (solo en tiempo
  real). Con `--uptime-inicial 0` es el reinicio con hueco (S-R) de D01 y D18.
- Con `--inicio`, o con `captured_at` en el CSV, las horas son **exactas** y los mensajes salen
  seguidos (`--pausa-s`), cada uno después del acuse del anterior, para que la ventana del modelo
  encuentre guardadas las lecturas previas. Una hora en el futuro (más de 60 s) se rechaza antes de
  publicar, porque el backend la descartaría (`timestamp_futuro`).

Identidad: cada ejecución usa un `boot_id` nuevo (el epoch actual) y `seq_no` desde 1. Volver a
ejecutar el script **no** simula un reinicio con hueco: la ventana del modelo preventivo solo depende
del `device_id` y de las horas de las lecturas (`uc/predecir_riesgo_preventivo.py`,
`features_en_instante`), no del `boot_id` ni del `uptime_s`. El S-R necesita el hueco (`--hueco-s`).

Validación: cada mensaje se valida antes de publicarse con el contrato del backend
(`src/infrastructure/mqtt/contrato_v1.py`) y con el tope de 768 B. Los negativos de D19 (130 °C,
−60 °C, `power_on_reset` con valor, SHT31 a 200 °C) se escriben en un CSV y necesitan
`--permitir-invalidos --esperado rejected`. El backend tiene que responder `rejected` y auditar el
rechazo.

Acuse y evidencia: se espera el acuse de cada mensaje (`farmacias/<id>/ack`) hasta
`--acuse-timeout-s` desde su publicación, se correlaciona por (`boot_id`, `seq_no`) y se compara
`content_sha256` con la huella de los bytes enviados. Con `--registro` queda un CSV con cada mensaje,
su acuse, si la huella coincidió y la hora real de publicación (`publicado_en`). El script sale con 0
si todos los acuses tienen el estado esperado (`--esperado`, por defecto `committed`), con 1 si alguno
falta o no coincide, y con 2 si no publica nada (guarda de seguridad, entrada inválida o fallo al
conectar: red, TLS o CA).

### Recetas de los casos con condiciones de tiempo

Todas se ejecutan desde `infra-local/`, con el stack local y el modelo activo.

**D08 (IA colgada).** Backend local con `IA_PRUEBA_RETARDO_S=2` y, en la repetición,
`IA_PRUEBA_EXCEPCION=true`:

```bash
python scripts/publicador_prueba_mqtt.py valores --valores 5.0,8.5 --intervalo-s 0.5 --registro d08.csv
```

La lectura de 8,5 °C sale a su hora, como mucho 0,5 s después de la de 5,0 °C (t0 se redondea al
segundo, así que la primera puede salir algo tarde), aunque la inferencia de esta siga colgada: en
tiempo real la publicación no espera acuses. La columna `publicado_en` de `d08.csv` es la
evidencia: si entre las dos filas hay 1 s o más, el caso no prueba nada y se repite. No use `--inicio`
en D08: con horas exactas cada mensaje espera el acuse del anterior, que llega después de la
inferencia.

**D18 (excursión nueva con un episodio ATENDIDO abierto).** El caso solo prueba algo si los pasos 4 y
5 ocurren en `HISTORIAL_INSUFICIENTE`. Si el script se vuelve a ejecutar sin pausa no hay hueco, y con
un hueco de 3 min o menos la cobertura solo cae durante menos de 1 min: el modelo vuelve a clasificar
y D18 «pasa» sin ejercitar el defecto. Con lecturas cada 10 s y `features_en_instante`, un hueco de
5,5 min o más deja en `HISTORIAL_INSUFICIENTE` todas las lecturas de los 14 min siguientes a la
reconexión. Con 5 min justos hay tramos en los que vuelve a clasificar; por eso la receta usa 330 s.

1. Calentamiento y excursión, sin lecturas en rango después, para que el episodio siga abierto:
   `python scripts/publicador_prueba_mqtt.py excursion --base 90 --evento 1 --cola 0` (15 min a
   5,0 °C y una lectura de 8,5 °C) → alerta PENDIENTE.
2. Reconocer la alerta y registrar la acción (D10 paso 2) → ATENDIDA.
3. y 4. Reinicio con hueco (S-R) y lecturas en rango:

   ```bash
   python scripts/publicador_prueba_mqtt.py en_rango --lecturas 6 --temp 5.0 \
       --hueco-s 330 --uptime-inicial 0 --registro d18_paso4.csv
   ```

   El script espera 5,5 min sin conectarse ni publicar y luego envía como un nodo recién arrancado
   (`boot_id` nuevo, `seq_no` desde 1 y `uptime_s` desde 0). El hueco cuenta desde la última lectura
   del paso 1, así que es de al menos 5,5 min. Alternativa sin hueco: publicar los pasos 4 y 5 con
   `--time-quality unsynced` (`HISTORIAL_INSUFICIENTE` con `reloj_no_sincronizado`).
   **Antes del paso 5**, compruebe la precondición en
   `GET /api/lecturas?device_id=PRUEBA_P13_01&limite=10`: las lecturas del paso 4 tienen que estar con
   `estado_inferencia=omitida` y `motivo_no_inferencia` = `ventana_15min_sin_cobertura_suficiente` (o
   `reloj_no_sincronizado`). Si alguna está `completada`, la precondición no se cumplió y D18 no se da
   por pasado: repita con un hueco mayor.
5. Antes de que vuelva la cobertura, dentro de los 10 min siguientes a la reconexión:
   `python scripts/publicador_prueba_mqtt.py valores --valores 8.5`. Usa otro `boot_id`, lo que no
   cambia la ventana. Esperado: una alerta crítica PENDIENTE **nueva** en
   `GET /api/alertas?estado=pendiente`, el aviso encolado y el correo en Mailpit.

D01 usa el mismo S-R. Después de un calentamiento (`en_rango --lecturas 90`), una sola ejecución:
`excursion --base 30 --evento 1 --cola 90 --hueco-s 330 --uptime-inicial 0`. Tras el hueco son 5 min
a 5,0 °C, una lectura de 8,5 °C dentro de la ventana sin cobertura y 15 min más para observar cuándo
vuelve la clasificación.

### CSV de escenario

Una celda vacía toma el valor por defecto; `null` pone el valor a nulo. `captured_at` vacío solo se
admite con `time_quality=unsynced`. Con `offset_s` (segundos desde la primera lectura) las lecturas
salen en tiempo real; con `captured_at` las horas son exactas y no pueden estar en el futuro.

`d12_unsynced.csv` (D12: reloj sin sincronizar; el backend acusa `committed`):

```csv
offset_s,temp_ds18b20,time_quality
0,5.0,unsynced
10,5.0,unsynced
20,8.5,unsynced
```

```bash
python scripts/publicador_prueba_mqtt.py csv --archivo d12_unsynced.csv --registro d12.csv
```

Las dos primeras quedan en `HISTORIAL_INSUFICIENTE` con `reloj_no_sincronizado` y la de 8,5 °C
produce `excursion_critica`. Lo mismo sin CSV: `valores --valores 5.0,5.0,8.5 --time-quality unsynced`.

`d19_implausibles.csv` (D19: todas incumplen el contrato v1.0 y el backend las rechaza):

```csv
offset_s,temp_ds18b20,ds18b20_fault,temp_sht31
0,130,,
10,-60,,
20,85,power_on_reset,
30,5.0,,200
```

```bash
python scripts/publicador_prueba_mqtt.py csv --archivo d19_implausibles.csv \
    --permitir-invalidos --esperado rejected --registro d19.csv
```

No mezcle en un CSV filas válidas y negativos: con `--esperado rejected` las válidas (`committed`)
harían salir el script con 1. Si hace falta mezclarlas, use `--esperado committed,rejected` y revise
el estado de cada fila en el registro. `tests/unit/test_p13_publicador_prueba_mqtt.py` comprueba que
estos dos ejemplos se pueden publicar tal cual.

### Lo que no hace

- No sirve para producción ni para ESP32_BANCO_01, ni siquiera en local. Los casos del plan en Railway
  usan el nodo físico (sección 3.4).
- No genera datos de entrenamiento. Sus lecturas quedan con un `device_id` de prueba y no deben
  aparecer en el manifiesto de corridas (sección 2). Los datos de entrenamiento salen del nodo de banco
  con PE-INC-01 (P-11).

## 2. Exportador de corridas (P-1)

### Salida

Son las columnas del plan, en este orden. Las que lee `ingesta.importar_coldcare_csv` van primero:

| Columna | Origen en `thermal_readings` |
|---|---|
| `device_id` | `device_id` |
| `timestamp` | `COALESCE(timestamp_estimado, timestamp)` en UTC con milisegundos: la hora de captura, o la reconstruida si el nodo arrancó sin reloj |
| `temperatura_interna` | `temperatura_interna` (vacía con el DS18B20 en falla) |
| `apertura_refrigerador` | `true` / `false` / vacía (sin MC-38) |
| `time_quality` | Valor interno: `synced`, `backfilled`, `unsynced` o vacío (firmware anterior) |
| `ds18b20_status` | `SENSOR_FAULT` si no hay temperatura interna, `OK` si la hay |
| `sensor_fault` | `payload.sensor_fault` del contrato v1 (DS18B20 **o** SHT31); vacía en firmware anterior |
| `corrida_id` | Del manifiesto (abajo) |
| `temperatura_ambiental`, `humedad_ambiental` | SHT31, para la ablación con y sin ese sensor |
| `boot_id`, `seq_no` | Identidad de la lectura (trazabilidad) |
| `origen_hora` | `nodo`, `reconstruida` (`timestamp_estimado`) o `recepcion` (sin hora ni reconstrucción) |

Solo se exportan lecturas del nodo (`origen = 'dispositivo'`) sin `simulated`. Las simuladas se cuentan
en el resumen como `simuladas_omitidas`.

El importador del pipeline descarta y cuenta todas las `unsynced`, también las reconstruidas, y deja
como hueco las lecturas con `ds18b20_status = SENSOR_FAULT`. Desde este cambio, `sensor_fault` solo
cuenta cuando la fila no trae `ds18b20_status`. Antes, una falla del SHT31 sola borraba una temperatura
interna válida (`tests/unit/test_preventivo_datos.py`).

### `corrida_id`: manifiesto, no columna

**Decisión:** `corrida_id` sale de un manifiesto CSV con una fila por hoja de corrida (sección 3.2 del
plan). No se agrega ninguna columna ni migración. Motivos:

1. `thermal_readings` es de solo anexado. La migración `0018_evidencia_solo_anexado` prohíbe DELETE, y
   UPDATE salvo el de `timestamp_estimado`. Etiquetar lecturas ya guardadas exigiría modificar
   evidencia de la cadena regulatoria.
2. El nodo no sabe en qué corrida está, así que tampoco se puede etiquetar al ingerir sin otro canal.
   La corrida la define quien la ejecuta, con su inicio y su fin, igual que `experiment_actions.csv`
   en los datos de Hohenheim.
3. El manifiesto se versiona con la evidencia, se revisa en un PR y se puede corregir sin tocar la
   base. Las particiones del pipeline (`pp/particion.py`) ya trabajan con `corrida_id`.

Formato (UTF-8, con cabecera):

```csv
corrida_id,device_id,inicio,fin,tipo,uso,temperatura_inicial,mqtt_host,responsable,observaciones
C01,ESP32_BANCO_01,2026-11-20T14:00:00Z,2026-11-20T16:05:00Z,S-C,entrenamiento,4.8,192.168.18.21,DS,
C02,ESP32_BANCO_01,2026-11-20T17:00:00Z,2026-11-20T19:10:00Z,S-P5,prueba,4.6,192.168.18.21,DS,
C03,ESP32_BANCO_01,2026-11-21T09:00:00Z,2026-11-21T09:10:00Z,S-B,excluida,4.9,192.168.18.21,DS,solo regla
```

- Columnas obligatorias: `corrida_id` (letras, dígitos, `_`, `.` o `-`, hasta 50, sin `#`), `device_id`,
  `inicio` y `fin`. Las horas tienen que llevar la zona explícita (`Z` o `+00:00`), para no confundir
  la hora de Lima con UTC. La ventana es [inicio, fin) y se compara con la hora de captura
  (`COALESCE`).
- Dos corridas del mismo dispositivo no pueden solaparse. Una lectura nunca cae en dos corridas.
- `uso` es opcional: `entrenamiento`, `validacion`, `prueba` o `excluida`. Una corrida `excluida` no se
  exporta; S-B, por ejemplo, prueba solo la regla y queda fuera del dataset. El resumen trae `prueba` y
  `validacion`, las listas para `corridas --prueba` y `corridas --validacion`. Las corridas de
  validación solo eligen el umbral y no ajustan el modelo (P-3, `09_REPRODUCIBILITY.md`); sin ellas, el
  umbral se elige dejando una corrida fuera.
- `tipo`, `temperatura_inicial`, `mqtt_host`, `responsable` y `observaciones` pasan al resumen tal
  cual, para cruzarlos en A03 (todas las corridas en el broker local).

### Uso

```bash
export DATABASE_URL=postgresql+asyncpg://...     # base del stack local; no se imprime
python -m scripts.exportar_corridas --manifiesto evidencias/hojas_corrida.csv \
    --salida corridas.csv --resumen corridas_resumen.json
# --prueba y --validacion: los valores `prueba` y `validacion` de corridas_resumen.json (p. ej. C02,C05 y C04)
python -m src.infrastructure.ai.prediccion_preventiva corridas --csv corridas.csv \
    --prueba C02,C05 --validacion C04 --salida corridas.json
# P-2: el mismo entrenamiento, registrado en models/preventivo/<V>/ (ver IA_PREVENTIVA_ACTIVACION.md, paso 3)
python -m src.infrastructure.ai.prediccion_preventiva corridas --csv corridas.csv \
    --prueba C02,C05 --validacion C04 --registrar --version rf-corridas-h30-termico-1.0.0 --salida corridas.json
```

El resumen da, por corrida, `filas`, `ds18b20_sensor_fault`, `unsynced`, `simuladas_omitidas`, la
primera y la última lectura, y `corridas_sin_lecturas`, que suele indicar una ventana mal anotada o un
nodo apuntando a otro broker. El CSV se escribe primero en `<salida>.parcial` y se renombra solo si la
exportación termina; no sobrescribe un archivo existente sin `--sobrescribir`.

Solo lectura: en PostgreSQL la sesión se abre con `default_transaction_read_only=on` y cada transacción
empieza con `SET TRANSACTION READ ONLY`. En SQLite se abre el archivo con `mode=ro`. Otros dialectos se
rechazan. Lo que prueba el solo lectura es que un `DELETE` hecho por ese motor falla en los dos
motores. Que el archivo SQLite quede idéntico byte a byte tras exportar solo confirma que el
exportador no escribe por su cuenta: un `SELECT` tampoco escribiría con un motor normal.

## 3. Referencias para el plan de pruebas

El plan (`auditoria/PLAN_PRUEBAS_IA.md`) no se edita desde este repositorio. Al actualizarlo, estas son
las rutas que corresponde citar en sus secciones 3 y 6:

| Sección del plan | Qué cita | Ruta |
|---|---|---|
| 2.1 P-1 / 2.2 fase A | Exportador de corridas | `scripts/exportar_corridas.py`; formato en este documento, sección 2 |
| 2.1 P-13 / 2.2 fases B–F | Publicador de prueba | `infra-local/scripts/publicador_prueba_mqtt.py` |
| 3.1 Local automatizado | Alta del dispositivo de prueba y publicador en el stack Docker | `infra-local/scripts/crear_dispositivo.py PRUEBA_P13_01`; `infra-local/scripts/publicador_prueba_mqtt.py` |
| 3.2 Banco (hoja de corrida) | Una fila del manifiesto por hoja: `corrida_id`, inicio y fin en UTC, tipo, temperatura inicial, `mqtt_host`, responsable; S-B con `uso=excluida` | `scripts/exportar_corridas.py --manifiesto` |
| 3.3 Volumen mínimo | Conteo por corrida (`filas`, fallas, `unsynced`) | `--resumen` del exportador |
| 5 A03 | «CSV de P-1» | Salida de `scripts/exportar_corridas.py` → `prediccion_preventiva corridas` |
| 5 D01, D08, D09, D12, D18, D19 (local) | «P-13» | `infra-local/scripts/publicador_prueba_mqtt.py` (escenarios de la sección 1) |
| 6.3 R-A, verificación «una excursión sigue generando alerta crítica» (en local) | Excursión con un dispositivo de prueba, sin DEMO ni `simulated` | `publicador_prueba_mqtt.py valores --valores 5.0,8.5` |
| 6.4 IA-C08 en local (obligatoria antes del GO) | Lecturas exactas antes y después de R-A y de la desactivación de la versión (P-12), para comprobar que la regla de 2–8 °C sigue decidiendo | `publicador_prueba_mqtt.py en_rango` + `valores --valores 8.5` |

En producción (sección 3.4) el publicador no se usa nunca: los casos con nodo en Railway usan el
ESP32_BANCO_01 físico. El exportador sí sirve para el Experimento D (telemetría real en modo sombra),
porque solo lee; el procedimiento está en `09_REPRODUCIBILITY.md`.
