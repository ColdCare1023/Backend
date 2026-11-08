# ColdCare: cambios al short paper y plan de pruebas de banco

5 de noviembre de 2026. Documento para quien redacte el paper (persona o IA). Dice qué hay que cambiar
en el texto, cómo se hará cada prueba con el nodo real, qué archivos deja cada corrida y qué resultado
se espera. Así cada cifra se puede pasar al paper sin rehacer cuentas.

**Decisiones ya tomadas (5 nov.):**
- **Intervalo de muestreo:** se mantiene en **10 s**. No se cambia a 30 s.
- **Corte de Wi-Fi:** se hace apagando el **hotspot del celular**, al que están conectados el nodo y la laptop.
- **Repeticiones:** **N = 5** en los escenarios cortos y **N = 1** en los largos.
- **Duración de S1:** **12 h**, no 24 h.

---

## Parte 1. Cambios en el paper

### 1.1 Configuración experimental (sección de método)

Reemplazar cualquier mención a un "nodo virtual" como única evidencia por una descripción del **banco
físico**:

| Elemento | Texto sugerido |
|---|---|
| Nodo | ESP32-D0WD-V3 (DevKitC V4) en placa perforada soldada, con sonda DS18B20 (GPIO4, 12 bits, pull-up de 4,7 kΩ), SHT31 (I²C 0x44) y contacto magnético MC-38 (GPIO27, pull-up interno) |
| Firmware | ThermoTrace Edge v1.2.0, entorno `esp32dev`, commit `c8c0cac` del repositorio IoT (5 nov. 2026) |
| Intervalo de muestreo | **10 s** en todas las corridas |
| Buffer | LittleFS, 300 segmentos de 100 registros = **30 000 registros**. A 10 s son **83,3 h (3,47 días)**; a 30 s serían 10,4 días (configuración de campo prevista) |
| Broker | EMQX 5.8 local, TLS en el puerto 8883, autenticación usuario = `device_id` y ACL por tópico, QoS 1 |
| Backend y base | FastAPI y PostgreSQL 16 en Docker, en la misma laptop |
| Acuse | Doble: PUBACK de QoS 1 del broker y **acuse de aplicación** que publica el backend en `farmacias/<id>/ack` después del COMMIT. El nodo borra el registro solo al recibir el segundo |
| Red | Hotspot de un teléfono Android al que se conectan el nodo y la laptop. Un "corte de Wi-Fi" consiste en apagar el hotspot |
| Reloj | El nodo usa SNTP y la laptop usa la hora de Windows. La marca de captura del nodo tiene **resolución de 1 s** |
| Detección "sin datos" | Incidente `sin_datos` cuando el silencio llega a 3 × intervalo (**30 s**). El vigilante corre cada 30 s, así que la detección cae entre 30 y 60 s |
| Repeticiones | N = 5 por escenario corto, N = 1 en S1 (12 h), S3 (6 h) y saturación |

Agregar una frase del tipo: *"Todas las corridas se hicieron en banco de laboratorio; no constituyen un
piloto en farmacia"*. Si después hay instalación piloto, se reporta aparte.

### 1.2 Tabla de escenarios (reemplaza la actual)

Con el intervalo de 10 s, los conteos esperados cambian. La versión actual dice "20 registros" y
"90 s (3 ciclos de 30 s)", que corresponden a 30 s.

| Caso | Qué se hace | Qué se mide | Criterio de éxito |
|---|---|---|---|
| S1 Operación normal | 12 h conectado, puerta cerrada, sonda dentro del refrigerador | Registros capturados, guardados, faltantes y duplicados. Latencia | 0 faltantes, 0 duplicados (≈ 4 320 registros) |
| S2 Corte de Wi-Fi corto | Hotspot apagado 10 min | Registros diferidos y su reenvío. Orden. Incidente `sin_datos` | Los ≈ 60 registros del corte llegan, 0 faltantes, 0 fuera de orden |
| S3 Corte largo | Hotspot apagado 6 h | Ocupación máxima del buffer y tiempo de vaciado | ≈ 2 160 registros diferidos (7,2 % del buffer), 0 faltantes, broker sin mensajes descartados |
| S5 Reinicio durante un corte | Corte, reset del nodo a mitad del corte, vuelta de la red | `boot_id` y `seq_no` antes y después | `boot_id` sube en 1, `seq_no` vuelve a empezar, 0 faltantes por `boot_id`, 0 duplicados |
| S6 Caída del backend antes del COMMIT | Inyección de falla `salir_una_vez` justo antes del COMMIT | Reenvíos del registro afectado | El registro llega ≥ 2 veces al broker y se guarda **una sola vez** |
| Línea base sin acuse de aplicación | Falla del COMMIT sostenida 2 min (`excepcion`), luego se repara | Registros que llegaron al backend sin COMMIT | El diseño pierde 0. Un diseño solo con PUBACK habría perdido todos los registros de la ventana |
| S7 Broker caído | EMQX detenido 30 min | Conservación en el buffer, reintentos, `sin_datos` | ≈ 180 registros diferidos, todos llegan al volver, 0 faltantes |
| S9 Puerta abierta | Imanes separados 5 min | Tiempo hasta la primera lectura con la puerta abierta y hasta el estado "abierta prolongada" (60 s) | Estado prolongado visible en menos de 2 min |
| S12a Sonda en falla | Desconectar la línea de datos del DS18B20 | Incidente `sensor_fault` | Se abre en la 2.ª lectura en falla (≤ 20 s + latencia) y se cierra al volver |
| S12b Nodo sin energía | Desenchufar el nodo 3 min | Incidente `sin_datos` | Se abre entre 30 y 60 s después de la última lectura y se cierra al volver |
| Latencia extremo a extremo | Registros en línea de S1 (≥ 1 000) | Mediana y p95 de captura→COMMIT | Se reporta sin umbral |
| Error de la sonda | Baño de hielo (punto de hielo, 0,0 °C) | Error medio absoluto del DS18B20 | Dentro de ±0,5 °C (especificación del fabricante) |
| Saturación del buffer | Llenado sintético hasta 30 000 con el broker caído (ADR-18) | Comportamiento al llenarse | Descarta el más antiguo, cuenta la pérdida y emite el evento `buffer_saturado` |

**Cambios de fondo respecto del texto actual:**
1. **S12 se divide en dos casos.** Quitar el DS18B20 **no** produce "NO_DATA": el nodo sigue
   enviando lecturas con la falla marcada, y el backend abre `sensor_fault` en la segunda lectura
   seguida en falla. `sin_datos` (NO_DATA) aparece solo cuando el nodo deja de enviar. Por eso hay
   S12a y S12b.
2. **S9 no genera una "alerta".** El backend no abre una alerta ni un incidente por puerta abierta.
   Marca la lectura y el panel muestra "abierta prolongada" al pasar los **60 s**
   (`PUERTA_UMBRAL_PROLONGADA_SEGUNDOS`). El texto debe decir *"estado de puerta prolongada visible
   en el panel"*, no "alerta".
3. **La línea base sin acuse es contrafactual**, salvo que se compile una variante del firmware.
   Con el firmware actual se mide cuántos registros **llegaron al backend y no quedaron confirmados**
   durante la falla. Esos registros se habrían perdido en un diseño que borra el registro al recibir
   el PUBACK, y el diseño evaluado los reenvía. Hay que decirlo así: *"pérdida estimada de la línea
   base"*, no "pérdida medida".
4. **La saturación es sintética.** Llenar 30 000 registros reales tomaría 83 h. Se usa el comando de
   banco `buffer llenar <n>` con una identidad de banco aparte (`ESP32_BANCO_02`), con datos marcados
   `simulated: true`. Hay que declararlo en el método.
5. **El error de sonda aplica solo al DS18B20.** El SHT31 no es sumergible. Para el SHT31 se reporta
   su **diferencia con el DS18B20 en el mismo aire** (sesgo), no un error absoluto. Si se consigue
   un termómetro de referencia calibrado, se reporta además el error contra ese termómetro.

### 1.3 Definiciones de métricas (agregar al método)

- **Registro capturado:** cada par (`boot_id`, `seq_no`) que generó el nodo.
- **Faltante:** un `seq_no` que no está en la base, entre el mínimo y el máximo de su `boot_id`.
- **Duplicado:** el mismo (`device_id`, `boot_id`, `seq_no`) guardado dos veces. La base lo impide con
  un índice único, así que el número esperado es siempre 0.
- **Reenvío:** el mismo (`boot_id`, `seq_no`) recibido más de una vez por el broker. Es el mecanismo
  funcionando, no un error. Se cuenta con el registro de la escucha del broker.
- **Diferido:** un registro que se guardó en el buffer y se envió después, con latencia mayor a 2 ×
  intervalo (20 s).
- **Latencia:** `created_at` (COMMIT en PostgreSQL) menos la hora de captura del nodo. Se reporta la
  mediana y el p95 **solo de los registros en línea** (≤ 20 s), y aparte los diferidos.
- **Tiempo de detección:** hora del evento provocado (marca del operador) hasta la primera lectura que
  lo refleja, y hasta la apertura del incidente o el cambio de estado.
- **Ocupación máxima del buffer:** el número de diferidos de la corrida, y su porcentaje sobre 30 000.
  Se contrasta con el campo `pendientes` del primer evento `estado_nodo` tras reconectar.
- **Tiempo de vaciado:** desde el COMMIT del primer diferido hasta el COMMIT del último.

### 1.4 Resultados (secciones 5.1 y 5.4)

El paper debe quedar con estas tablas y figuras, que se llenan con los resúmenes de las corridas (ver
Parte 2.4):

- **Tabla R1. Integridad por escenario:**
  - **Columnas:** caso, N, capturados, guardados, faltantes, duplicados, reenvíos y diferidos.
  - **Valores:** media ± desviación estándar, con el mínimo y el máximo entre repeticiones.
- **Tabla R2. Tiempos:**
  - **Detección:** de `sin_datos` en S2, S7 y S12b, de `sensor_fault` en S12a y de la puerta en S9.
  - **Vaciado:** en S2, S3 y S7.
- **Tabla R3. Latencia:** n, mediana, p95 y máximo, en línea y diferidas.
- **Tabla R4. Sonda:**
  - **DS18B20 en baño de hielo:** error medio absoluto y error máximo.
  - **SHT31 frente al DS18B20:** el sesgo medio.
- **Tabla R5. Línea base:** registros afectados por la falla del COMMIT, perdidos con el diseño (0)
  frente a perdidos estimados con un diseño solo con PUBACK.
- **Figura sugerida:** un corte S2 en el tiempo. En el eje x la hora de captura y en el eje y la
  latencia. Se ve el escalón de los diferidos y el vaciado.

### 1.5 Inconsistencias a corregir

| Hoy dice | Cambiar por |
|---|---|
| Mecanismos verificados en un "nodo virtual" | Banco físico con ESP32_FARMACIA_01 (fw v1.2.0). El nodo virtual y el banco mosquitto quedan como verificación previa (13 de 13 confirmados y 0 de 6 sin acuse en el banco mosquitto del 25 de septiembre) |
| Intervalo de 30 s | 10 s en el banco. La autonomía del buffer es de 3,47 días a 10 s y de 10,4 días a 30 s |
| 998, 318 y 26 pruebas al 27 de octubre, y 134 de firmware al 4 de noviembre | Una sola fecha, después de volver a correr las suites (Parte 2, paso 5) |
| No menciona el defecto F ni la prueba H | Anotarlos como limitaciones conocidas mientras sigan abiertos |
| S12 "NO_DATA a los 90 s" | S12a `sensor_fault` y S12b `sin_datos` a los 30–60 s |
| S9 "alerta en menos de 2 min" | Estado de puerta prolongada (60 s) visible en el panel |
| S1 de 24 h | S1 de 12 h |
| "Baño de hielo y referencia calibrada" para los dos sensores | DS18B20 en baño de hielo. SHT31 en aire, como sesgo frente al DS18B20 |

### 1.6 Limitaciones (agregar o ampliar)

1. Banco de laboratorio, no piloto en farmacia. Un solo nodo y un solo refrigerador.
2. El corte de Wi-Fi apaga un hotspot. Ese mismo corte deja la laptop sin red externa, pero el broker
   y el backend son locales y siguen funcionando.
3. Broker local (EMQX en Docker), no EMQX Cloud. La prueba H en la nube sigue pendiente.
4. N = 1 en los escenarios largos (S1, S3 y saturación).
5. La saturación usa datos sintéticos con identidad de banco.
6. La línea base sin acuse es contrafactual (salvo que se compile la variante).
7. La hora de captura tiene resolución de 1 s, y los relojes del nodo (SNTP) y de la laptop no están
   sincronizados entre sí con precisión de milisegundos.
8. El SHT31 no tiene un error absoluto contra una referencia calibrada (salvo que se consiga una).
9. El clasificador de IA en modo sombra: resultado negativo (3,2 falsas alarmas por día, límite
   inferior del IC 0,478). Mantenerlo o quitarlo es una decisión pendiente.

---

## Parte 2. Plan de implementación de las pruebas

### 2.1 Preparación común a todas las corridas con el nodo

1. **Hardware:**
   - Sonda DS18B20 **dentro del refrigerador** (2–8 °C). A temperatura ambiente el backend abre una
     alerta crítica en cada lectura, y eso contamina S1 y S9.
   - Imanes del MC-38 **juntos** (puerta cerrada), salvo en S9.
   - Nodo conectado al USB de la laptop o a un cargador. Durante las corridas no se abre el puerto
     serie. Solo se abre para configurar el nodo y en la saturación.
2. **Red:** el hotspot "S25 Ultra de Brenda" encendido y el celular cargando. La laptop y el nodo
   conectados a él. La laptop tiene la IP **10.123.44.175**, que está en el certificado del broker.
3. **Stack local** (`Backend/infra-local`, proyecto `coldcare-develop`):
   - PostgreSQL, EMQX y backend arriba (`docker compose -p coldcare-develop --profile backend up -d`).
   - Backend en `127.0.0.1:8000/health` respondiendo 200.
   - Panel en `localhost:5173`.
4. **Laptop:** que no se suspenda durante la corrida.
5. **Comprobación previa:** el nodo envía, la cola está en 0 pendientes y no hay incidentes abiertos del
   dispositivo.

### 2.2 Herramientas y cómo se guardan los datos

Dos scripts en `Backend/infra-local/scripts/`. Solo **leen**: no modifican el sistema que se mide.

- **`paper_escucha.py`:** se suscribe al broker como `backend_service`, que tiene permiso de lectura.
  Escribe una fila por cada mensaje de lectura o evento que llega. Sirve para contar reenvíos, porque
  la base descarta los duplicados sin avisar.
- **`paper_corrida.py`:** maneja la corrida.

```bash
# desde Backend/infra-local
python scripts/paper_corrida.py inicio S2 1 --nota "hotspot 10 min"
python scripts/paper_corrida.py marca "hotspot apagado"     # cada evento provocado, en el momento
python scripts/paper_corrida.py marca "hotspot encendido"
python scripts/paper_corrida.py fin                           # exporta y resume
python scripts/paper_corrida.py resumen evidencia/paper/<carpeta>   # recalcula sin tocar la base
```

Cada corrida queda en `Backend/infra-local/evidencia/paper/<CASO>_r<rep>_<AAAAMMDD-HHMM>/`. Esa carpeta
está fuera de git, así que hay que respaldarla en un zip junto con el paper.

| Archivo | Contenido |
|---|---|
| `corrida.json` | caso, repetición, `device_id`, intervalo, inicio y fin en UTC, nota |
| `marcas.csv` | `utc, marca`: los eventos que provocó el operador |
| `mqtt.csv` | `llegada_utc, topico, boot_id, seq_no, captured_at, tipo, time_quality, detalle`: un mensaje por fila, con reenvíos y eventos (`estado_nodo` trae `pendientes`, `ocupacion_pct` y `pubacks_sin_ack` en `detalle`) |
| `lecturas.csv` | **una fila por registro guardado**: `boot_id, seq_no, captured_utc, received_utc, commit_utc, latencia_s, time_quality, temp_ds18b20, temp_sht31, humedad, puerta_abierta, puerta_racha_s, puerta_abierta_s_ciclo, sensor_fault, ds18b20_fault, sht31_fault, nivel_riesgo, riesgo_efectivo` |
| `incidentes.csv` | `tipo, abierto_utc, resuelto_utc, umbral_segundos, detalle` de la ventana |
| `alertas.csv` | `nivel_riesgo, estado, creada_utc, iniciada_utc, mensaje` de la ventana |
| `resumen.json` / `resumen.md` | las cifras de la corrida: guardadas, faltantes, duplicadas, diferidas, fuera de orden, mensajes y reenvíos en el broker, latencia (mediana, p95, máx; total y en línea), detalle por `boot_id` y, por cada marca, los segundos hasta el primer incidente y la primera alerta |

La ventana de la corrida se define por la **hora de captura** del nodo, entre `inicio` y `fin`. Por eso
`fin` se ejecuta **después** de que se vacía el buffer (0 pendientes).

### 2.3 Procedimiento por escenario

Duración estimada por repetición entre paréntesis.

**S1. Operación normal (12 h, N = 1)**
1. **Inicio:** `inicio S1 1`.
2. **Durante:** no tocar nada durante 12 h. Puede correr de noche.
3. **Cierre:** `fin`.
4. **Resultado esperado:** unos 4 320 registros, 0 faltantes, 0 duplicados, 0 diferidos. Si hay
   reenvíos, se reportan.
5. **Latencia:** de esta corrida salen las ≥ 1 000 lecturas de latencia en línea (mediana y p95).

**S2. Corte de Wi-Fi corto (≈ 20 min, N = 5)**
1. **Inicio:** `inicio S2 <r>` y esperar 2 min con el nodo conectado.
2. **Corte:** `marca "hotspot apagado"` y apagar el hotspot.
3. **Esperar:** 10 min. La laptop queda sin internet, pero el sistema local sigue.
4. **Vuelta:** encender el hotspot y, apenas la laptop reconecte, `marca "hotspot encendido"`.
5. **Vaciado:** esperar a que se vacíe el buffer (unos 2–5 min) y luego `fin`.
6. **Resultado esperado:**
   - unos 60 diferidos, 0 faltantes, 0 fuera de orden;
   - incidente `sin_datos` abierto entre 30 y 60 s después del corte y cerrado al volver.
7. **Comprobar:** que la laptop vuelva con la IP 10.123.44.175. Si cambia, la corrida no vale y se
   repite.

**S3. Corte largo (≈ 6,5 h, N = 1)**
- **Procedimiento:** igual que S2, pero con el hotspot apagado 6 h.
- **Contadores del broker:** antes y después se guardan con
  `docker exec coldcare-develop-emqx-1 emqx ctl metrics`, para revisar los mensajes descartados
  (`messages.dropped`).
- **Resultado esperado:**
  - unos 2 160 diferidos (7,2 % del buffer) y 0 faltantes;
  - tiempo de vaciado medido (el nodo envía hasta 20 por ciclo de drenaje);
  - 0 mensajes descartados en el broker.

**S5. Reinicio durante un corte (≈ 15 min, N = 5)**
1. **Inicio:** `inicio S5 <r>`, y luego `marca "hotspot apagado"` y apagar el hotspot.
2. **Reset a mitad del corte:** a los 3 min, `marca "reset"` y pulsar EN en el ESP32 (o desenchufar y
   volver a enchufar).
3. **Vuelta:** a los 3 min más, encender el hotspot, `marca "hotspot encendido"`, esperar el vaciado y
   luego `fin`.
4. **Resultado esperado:**
   - dos `boot_id` consecutivos en el resumen;
   - el segundo empieza en `seq_no` 1;
   - 0 faltantes en cada `boot_id` y 0 duplicados;
   - los diferidos del boot anterior al reset también llegan, porque el buffer persiste en LittleFS.

**S6. Caída del backend antes del COMMIT (≈ 5 min, N = 5)**
1. **Activar la falla:** levantar el backend con la inyección de falla, apuntando a la **misma base**
   para que la evidencia quede junta:
   `POSTGRES_DB_PRUEBAS=<POSTGRES_DB> MQTT_PRUEBA_FALLO_ANTES_COMMIT=salir_una_vez docker compose -p coldcare-develop -f docker-compose.yml -f docker-compose.pruebas.yml --profile backend up -d backend`.
2. **Inicio:** `inicio S6 <r>`.
3. **Caída:** la primera lectura tras armar el interruptor tumba el backend antes del COMMIT. Anotar
   `marca "backend caido"` y volver a levantarlo con `docker start coldcare-develop-backend-1`.
4. **Cierre:** esperar el reenvío y luego `fin`.
5. **Repetir:** para la siguiente repetición, borrar la marca `/pruebas/fallo_una_vez` dentro del
   contenedor.
6. **Resultado esperado:** el (`boot_id`, `seq_no`) afectado aparece ≥ 2 veces en `mqtt.csv` y **una
   vez** en `lecturas.csv`. 0 faltantes, 0 duplicados.
7. **Al terminar:** volver al backend normal, sin el archivo de pruebas.

**Línea base sin acuse de aplicación (≈ 6 min, N = 5)**
1. **Inicio:** `inicio BASE <r>`.
2. **Falla sostenida:** levantar el backend con `MQTT_PRUEBA_FALLO_ANTES_COMMIT=excepcion`. Cada COMMIT
   falla, pero el backend sigue vivo, recibe el mensaje y no confirma.
3. **Ventana:** `marca "falla on"`, esperar 2 min, levantar el backend normal y `marca "falla off"`.
4. **Cierre:** esperar el vaciado y luego `fin`.
5. **Resultado esperado:** unas 12 lecturas afectadas, todas guardadas al final (diseño: 0 perdidas).
6. **Pérdida estimada de la línea base:** el número de (`boot_id`, `seq_no`) cuya **primera** llegada
   al broker cae dentro de la ventana. Un diseño que borra el registro con el PUBACK los habría
   perdido.
7. **Opcional:** si se compila una variante del firmware que borre el registro con el PUBACK, la
   pérdida se mide en vez de estimarse. Requiere un cambio en el firmware, fuera de este plan.

**S7. Broker caído (≈ 40 min, N = 5)**
1. **Inicio:** `inicio S7 <r>`.
2. **Caída:** `marca "broker detenido"` y `docker stop coldcare-develop-emqx-1`.
3. **Esperar:** 30 min.
4. **Vuelta:** `docker start coldcare-develop-emqx-1`, `marca "broker arriba"`. El backend se reconecta
   solo; si no, se reinicia.
5. **Cierre:** esperar el vaciado y luego `fin`.
6. **Resultado esperado:**
   - unos 180 diferidos y 0 faltantes;
   - reintentos con espera creciente en el nodo;
   - `sin_datos` abierto a los 30–60 s y cerrado al volver.

**S9. Puerta abierta (≈ 8 min, N = 5)**
1. **Inicio:** `inicio S9 <r>`, con 1 min de puerta cerrada.
2. **Apertura:** `marca "puerta abierta"` y separar los imanes 5 min.
3. **Cierre:** `marca "puerta cerrada"`, juntar los imanes, esperar 1 min y luego `fin`.
4. **De `lecturas.csv`:**
   - el tiempo hasta la primera lectura con `puerta_abierta = t`, esperado ≤ 10 s más la latencia;
   - el tiempo hasta la primera lectura con `puerta_racha_s` ≥ 60, que es el estado prolongado del
     panel, esperado unos 60–70 s;
   - el tiempo hasta que la lectura vuelve a `f` tras cerrar.
5. **Criterio:** estado prolongado en menos de 2 min.
6. **Captura:** se toma una captura de pantalla del panel como evidencia.

**S12a. Sonda en falla (≈ 5 min, N = 5)**
- **Requisito de hardware:** hoy la sonda está **soldada**. Para desconectarla sin desoldar hace falta
  un interruptor o un puente en la línea de datos (cable amarillo, entre E29 y C7). Si no se instala,
  S12a se omite y se declara.
- **Procedimiento:** `inicio S12a <r>`, `marca "sonda desconectada"` y abrir la línea 2 min.
  Luego `marca "sonda conectada"`, cerrarla, esperar 1 min y `fin`.
- **Resultado esperado:** `sensor_fault` abierto en la 2.ª lectura seguida en falla (≤ 20 s más la
  latencia) y cerrado con la primera lectura válida. Las lecturas en falla se guardan con
  `temp_ds18b20` vacío y `sensor_fault = true`. No es una pérdida.

**S12b. Nodo sin energía (≈ 6 min, N = 5)**
- **Procedimiento:** `inicio S12b <r>`, `marca "nodo desenchufado"` y desenchufar el nodo 3 min.
  Luego `marca "nodo enchufado"`, enchufarlo, esperar 2 min y `fin`.
- **Resultado esperado:** `sin_datos` abierto entre 30 y 60 s después de la última lectura y cerrado
  al volver. Al volver, un `boot_id` nuevo. Los registros no capturados mientras no había energía no
  cuentan como faltantes, porque no existen.

**Error de sonda (≈ 30 min, N = 5)**
1. **Preparar el baño:** hielo picado y un poco de agua en un recipiente aislado, revolver y esperar
   10 min a que se estabilice (punto de hielo, 0,0 °C).
2. **Inicio:** `inicio SONDA <r>`, `marca "sonda en hielo"`, sumergir la punta del DS18B20 sin tocar el
   fondo, revolviendo cada minuto.
3. **Cierre:** tras 5 min estable, `marca "fin estable"` y `fin`.
4. **Cálculo:** con las lecturas entre las marcas, el error medio absoluto, el error máximo y la
   desviación estándar frente a 0,0 °C.
5. **SHT31:** con las lecturas de S1 (mismo refrigerador), el sesgo medio `temp_sht31 − temp_ds18b20`.

**Saturación del buffer (≈ 1–2 h, N = 1, con el puerto serie abierto)**
1. **Procedimiento:** el de ADR-18 (`IoT/docs/decisiones_tecnicas.md`):
   1. vaciar la cola con la identidad real (`pendientes=0`);
   2. dar de alta `ESP32_BANCO_02` con `scripts/banco_dispositivo.py alta --device-id ESP32_BANCO_02`;
   3. cambiar la identidad del nodo por consola y reiniciar;
   4. detener el broker;
   5. `buffer llenar 30000` y unos registros más;
   6. ver el descarte del más antiguo y el evento `buffer_saturado`;
   7. levantar el broker y vaciar;
   8. volver a `ESP32_FARMACIA_01`;
   9. dar de baja el dispositivo de banco.
2. **Qué se registra:**
   - el log serie (`capturar_serial.py`);
   - el evento `buffer_saturado` (con rango `desde`/`hasta` y causa);
   - los contadores `perdidas_total` y `ocupacion_pct`;
   - el tiempo de vaciado de 30 000 registros.
3. **Resultado esperado:** al llenarse, descarta el registro más antiguo, cuenta la pérdida y emite
   `buffer_saturado`. Ningún sintético entra en la curva del equipo real.

### 2.4 Consolidación para el paper

Al terminar las N repeticiones de cada caso, un script de consolidación (por hacer: `paper_tabla.py`)
lee todos los `resumen.json` y genera:
- `evidencia/paper/tabla_resultados.csv`
- `evidencia/paper/tabla_resultados.md`

Para cada caso incluye N, y la media ± desviación estándar, el mínimo y el máximo de:
- guardadas, faltantes, duplicadas, reenvíos y diferidas;
- tiempos de detección y de vaciado;
- latencia.

Esas son las Tablas R1 a R5 del paper.

### 2.5 Pruebas de software (sin el nodo)

S4 (cadena de hash), S8, S10, S11 y S13 (reglas, clasificador y panel) se corren con las suites del
backend y del frontend, y con el simulador del backend. Ese mismo día hay que volver a correr todas
las suites y anotar el número y la fecha en un solo lugar del paper:
- **Backend:** pytest.
- **Frontend:** vitest.
- **Firmware:** pruebas nativas, en la CI o en local.

Para S4 (alteración de la cadena HMAC), la alteración se hace en la base de pruebas
(`coldcare_prueba`), nunca en la base de evidencia. Las tablas de evidencia son append-only.

### 2.6 Calendario

| Fechas | Corridas |
|---|---|
| 5–10 nov. | S2 ×5, S5 ×5, S1 (12 h, de noche) |
| 11–17 nov. | S3 (6 h), S6 ×5, línea base ×5, S7 ×5, S9 ×5, S12a ×5 (si se instala el interruptor), S12b ×5 |
| 18–22 nov. | Error de sonda ×5, saturación, suites de software, consolidación (`paper_tabla.py`) |
| 23–29 nov. | Pasar las cifras a las Tablas R1–R5, ajustar las secciones 5.1 y 5.4, corregir las inconsistencias de 1.5 |
| 30 nov. | Entrega de la v2 |

### 2.7 Qué debe hacer la IA que redacta el paper con esto

1. **Fuente de las cifras:** tomar cada número de `resumen.json` o de `tabla_resultados.md`, nunca
   estimarlo. Si una corrida falta, dejar el hueco marcado como `[pendiente]`.
2. **Separar medido de estimado:** decir siempre qué es **medido en banco físico**, qué es **estimado**
   (la línea base) y qué es **sintético** (la saturación).
3. **Reenvíos:** reportarlos como evidencia de que el mecanismo funciona. No son errores.
4. **Corridas invalidadas:** si una repetición se invalida (por ejemplo, la laptop cambió de IP), no
   se borra. Se reporta como invalidada y se repite.
5. **Fuera de este documento:** no afirmar nada sobre la prueba H en la nube ni sobre el defecto F de
   HU-44 hasta que estén cerrados.
