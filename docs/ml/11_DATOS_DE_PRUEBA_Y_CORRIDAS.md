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
  EMQX Cloud (`*.emqxsl.com`, `*.emqx.cloud`, …) y de Railway se rechazan siempre.
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

Todos aceptan las opciones comunes: `--intervalo-s` (10, como el banco), `--inicio`,
`--time-quality`, `--sin-sht31`, `--sin-mc38`, `--boot-id`, `--registro evidencia.csv` y `--dry-run`.

| Escenario | Qué publica | Casos del plan |
|---|---|---|
| `en_rango --lecturas N --temp T` | N lecturas a T °C | Calentamiento (≥ 90 lecturas = 15 min a 10 s), D18 paso 4 |
| `valores --valores 1.9,2.0,8.0,8.1` | Valores exactos (`null` = falla del DS18B20) | D09 límites, D08 (`5.0,8.5` con `--intervalo-s 0.5`), D01 paso 3 (`8.5`) |
| `rampa --lecturas N --desde A --hasta B` | Rampa lineal con ambos extremos | Anticipación, D03–D06 en local |
| `excursion --base --subida --pico --evento --bajada --cola` | Base en `--temp`, subida a `--pico` (8,5 por defecto), meseta, bajada y cola | D10, D18 pasos 1 y 5 |
| `puerta_abierta --base --evento --pico --cola` | Puerta abierta en subida y evento: `door_open_s` es lo abierto en el ciclo y `door_open_streak_s` la racha | Variante `termico_puerta`, D15 |
| `falla_sensor --base --evento --cola [--motivo crc_error]` | DS18B20 en `SENSOR_FAULT` durante el evento | D11 en local |
| `csv --archivo escenario.csv` | Una fila por lectura, con hora exacta (`captured_at`) o desplazamiento (`offset_s`) | Secuencias propias, D12 (`time_quality=unsynced`), D19 |

Horas:

- Sin `--inicio`, en **tiempo real**: la lectura i se captura en t0 + i·intervalo y se envía en ese
  instante, como el nodo. Es lo que hay que usar en D03, D07 y D10, donde cuentan la latencia y el
  correo, para que la alerta no salga marcada como diferida (`historica`).
- Con `--inicio`, o con `captured_at` en el CSV, las horas son **exactas** y los mensajes salen
  seguidos (`--pausa-s`). Una hora en el futuro (más de 60 s) se rechaza antes de publicar, porque el
  backend la descartaría (`timestamp_futuro`).

Identidad: cada ejecución usa un `boot_id` nuevo (el epoch actual) y `seq_no` desde 1. Para simular un
reinicio del nodo (S-R en D18) basta con ejecutar el script otra vez.

Validación: cada mensaje se valida antes de publicarse con el contrato del backend
(`src/infrastructure/mqtt/contrato_v1.py`) y con el tope de 768 B. Los negativos de D19 (130 °C,
−60 °C, `power_on_reset` con valor, SHT31 a 200 °C) se escriben en un CSV y necesitan
`--permitir-invalidos --esperado rejected`. El backend tiene que responder `rejected` y auditar el
rechazo.

Acuse y evidencia: tras cada mensaje se espera el acuse (`farmacias/<id>/ack`), se correlaciona por
(`boot_id`, `seq_no`) y se compara `content_sha256` con la huella de los bytes enviados. Con
`--registro` queda un CSV con cada mensaje, su acuse y si la huella coincidió. El script sale con 0 si
todos los acuses tienen el estado esperado (`--esperado`, por defecto `committed`) y con 1 si alguno
falta o no coincide.

Ejemplo de CSV (D12 y D19):

```csv
captured_at,temp_ds18b20,ds18b20_fault,temp_sht31,time_quality
2026-11-20T15:00:00Z,5.0,,,
,8.5,,,unsynced
2026-11-20T15:00:20Z,130,,,
2026-11-20T15:00:30Z,85,power_on_reset,,
2026-11-20T15:00:40Z,5.0,,200,
```

Una celda vacía toma el valor por defecto; `null` pone el valor a nulo. `captured_at` vacío solo se
admite con `time_quality=unsynced`.

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
- `uso` es opcional: `entrenamiento`, `prueba` o `excluida`. Una corrida `excluida` no se exporta;
  S-B, por ejemplo, prueba solo la regla y queda fuera del dataset. El resumen trae `prueba`, la lista
  lista para `corridas --prueba`.
- `tipo`, `temperatura_inicial`, `mqtt_host`, `responsable` y `observaciones` pasan al resumen tal
  cual, para cruzarlos en A03 (todas las corridas en el broker local).

### Uso

```bash
export DATABASE_URL=postgresql+asyncpg://...     # base del stack local; no se imprime
python -m scripts.exportar_corridas --manifiesto evidencias/hojas_corrida.csv \
    --salida corridas.csv --resumen corridas_resumen.json
# --prueba: el valor `prueba` de corridas_resumen.json (p. ej. C02,C05)
python -m src.infrastructure.ai.prediccion_preventiva corridas --csv corridas.csv \
    --prueba C02,C05 --salida corridas.json
```

El resumen da, por corrida, `filas`, `ds18b20_sensor_fault`, `unsynced`, `simuladas_omitidas`, la
primera y la última lectura, y `corridas_sin_lecturas`, que suele indicar una ventana mal anotada o un
nodo apuntando a otro broker. El CSV se escribe primero en `<salida>.parcial` y se renombra solo si la
exportación termina; no sobrescribe un archivo existente sin `--sobrescribir`.

Solo lectura: en PostgreSQL la sesión se abre con `default_transaction_read_only=on` y cada transacción
empieza con `SET TRANSACTION READ ONLY`. En SQLite se abre el archivo con `mode=ro`. Otros dialectos se
rechazan. Las pruebas comprueban que un `DELETE` hecho por ese motor falla en los dos motores y que el
archivo SQLite queda idéntico byte a byte.

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
