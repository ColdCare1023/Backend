## Notas por corrida

- **S2 r1:** las marcas del operador no quedaron registradas. El corte se tomó de los datos
  (`corte_inferido`).
- **S2 r4:** además del corte provocado hubo un **corte espontáneo de 95 s** a las 19:38 UTC
  (7 diferidas, 0 perdidas). La tabla usa el corte provocado, que es el de más diferidas.
- **S2 r5:** el cierre automático se adelantó al vaciado por un error de la herramienta, ya
  corregido: `fin` ahora espera el vaciado. La base se reexportó después del vaciado y sus cifras son
  válidas. `mqtt.csv` **no** incluye los mensajes del vaciado, así que en esa repetición los
  reenvíos **no se midieron**. El 0 de la tabla no cuenta.
- **S5 r2:** reinicios en bucle por watchdog después del reset (ver H1). Por eso hay 8 `boot_id`.
- **S5 r3:**
  - **Inicio con cola heredada:** empezó mientras el nodo vaciaba la cola de un corte no provocado
    de unas 2 h.
  - **Lecturas sin hora:** las 457 llegadas sin hora de captura (`sin_hora.csv`) son de esa cola
    y quedan fuera del análisis (ver H2).
  - **Log serie:** hay log serie (`serial.log`), y en esta repetición no se repitió el bucle.
- **S7 (r1–r5):**
  - **Duración del corte:** el corte medido (≈ 1 855 s) supera los 30 min con el broker detenido
    (1 800 s). La diferencia es la espera de reconexión del nodo y el tiempo de arranque de EMQX.
  - **Métricas de EMQX:** no quedaron guardadas porque el script usó un comando equivocado
    (`emqx ctl metrics`; el correcto es `emqx ctl broker metrics`). No afecta: con el broker detenido
    no le llega ningún mensaje que pueda descartar.
  - **Reconexión del backend:** no hizo falta reiniciar el backend en ninguna repetición.
  - **Puerta:** las 5 repeticiones corrieron con los imanes separados (`puerta_abierta = t`). No
    afecta lo que mide S7.
  - **Latencia (r5):** la mediana en línea de r5 es −0,06 s. Es el desfase entre el reloj del nodo y el
    de la laptop, menor a 1 s.

- **S9 (r1–r5):**
  - **Protocolo:** con horario fijo, los imanes del MC-38 se separaron 5 min con la puerta del
    refrigerador cerrada, y el script puso las marcas en el mismo segundo.
  - **Correcciones de la operadora:** el cierre de r2 se corrigió a 03:58:30Z y el de r3 a
    04:06:50Z, según lo que informó la operadora.
  - **r2:** el nodo registra el cierre real hacia las 03:58:25 y una reapertura de 1 s al reacomodar
    el imán. El error de duración de r2 (−13 s) refleja esa maniobra y no cuenta.
  - **Ni alertas ni incidentes en las 5 repeticiones:** confirma que la puerta abierta solo cambia
    el estado del panel.

- **S12b (r1–r5):**
  - **Protocolo:** el nodo se alimentó con un cargador de pared de 5 V con interruptor. Se cortó la
    energía con el interruptor (el LED del ESP32 se apaga), en horario fijo y por 3 min.
  - **Incidente previo:** el `sin_datos` de las 04:35Z, antes de r1, fue al mover el nodo del USB al
    cargador. Queda fuera de las corridas.

- **S1 r1 (12 h, 6 nov. 10:10:59 → 22:11:06 hora de Lima):**
  - **Arranque:** la laptop había estado apagada (sin datos de 02:57 a 10:09) y el nodo reinició
    (boot_id 1061). El script esperó a que la sonda volviera a 2,5–7,5 °C sin alerta abierta: arrancó
    con 7,5 °C, que bajó a ~3,4 °C en pocos minutos.
  - **Red:** laptop en WinPina 5G (192.168.18.114) y nodo en WinPina 4G (misma red).
  - **Alimentación (corregido el 7 nov.):** el nodo iba al **puerto USB de la laptop**, con el mismo
    cable de siempre, según la operadora. La nota de `corrida.json` dice "nodo en cargador de pared"
    porque el script la escribió por defecto; queda como dato crudo, pero es incorrecta. La laptop
    estuvo despierta las 12 h (keep-awake) y el nodo no se reinició ni una vez. Ver H10.
  - **Resultado:** 4 321 guardadas (seq 23–4343), 0 faltantes, 0 duplicadas en la base y 0 fuera de orden.
    No se abrió ninguna alerta: las 4 321 lecturas fueron `normal`. DS18B20: 3,92 ± 0,33 °C (3,31–7,38).
  - **Cortes no provocados:** hubo 15, de 31 a 46 s cada uno, todos entre las 15:38 y las 17:57 hora
    de Lima, con 25 diferidas recuperadas sin pérdida. Ver H9.
  - **Broker:** 32 reenvíos (misma boot_id + seq_no), todos descartados por la base, con 0 duplicadas.
  - **sin_datos:** un solo incidente (16:38 hora de Lima), detectado a los 39,5 s del último reporte
    y resuelto con la primera lectura.
  - **Latencia en línea (n = 4 296):** mediana 0,217 s, p95 1,56 s.
  - **Sesgo del SHT31:** no aplica. El SHT31 mide el ambiente fuera del refrigerador
    (25,5 ± 0,4 °C), no el interior. El paso 5 del plan suponía los dos sensores dentro.

- **S12a (S5 del paper, sonda desconectada): descartada en banco.**
  - **Por qué:** la sonda está soldada y no hay una resistencia de 100 Ω–1,5 kΩ para forzar la
    línea de datos a 0 sin riesgo para el GPIO4.
  - **Cómo queda cubierta:** solo con las pruebas de software:
    - firmware: `IoT/test/test_core/test_sensores.cpp`, decodificador del scratchpad con CRC,
      valor de power-on y rango;
    - backend: fixtures `telemetria_ds18b20_desconectado.json` y `telemetria_ds18b20_crc_error.json`.

## Hallazgos

**H1. Reinicio por watchdog en bucle con el nodo sin red (abierto).**
- **Qué se vio:**
  - **Tras un reset sin red (S5 r2):** el nodo se reinició 7 veces seguidas, una cada ≈ 34 s,
    hasta que volvió la red. El evento `arranque` posterior informa `reset_reason: TASK_WDT`.
  - **Sin reset, en un corte no provocado de unas 2 h (5 nov., 21:37–23:33 UTC):** ocurrió lo
    mismo, unos 47 arranques (boots 908–954) con duraciones de ≈ 34 s o ≈ 140 s.
  - **En cortes de 10 min sin reset (S2 ×5) no ocurrió.**
- **Efecto sobre los datos:** **ninguna lectura perdida ni duplicada**, porque el buffer persiste.
  Se pierden los eventos en RAM (los `arranque` de cada reinicio) y hay una brecha de captura de
  unos 4 s por reinicio.
- **Tercera ocurrencia (6 nov., 00:13–00:17 UTC):** al pasar a WinPina 4G, el nodo quedó en una
  red **con Wi-Fi** y la laptop seguía en el hotspot, así que el broker era inalcanzable. Hubo
  5 reinicios (boots 958–962), uno cada ≈ 40 s. Sugiere que el bucle aparece con **Wi-Fi asociado y
  broker inalcanzable**, más que sin Wi-Fi.
- **En S7 (broker detenido 30 min ×5, Wi-Fi y laptop alcanzables) no ocurrió:** un solo arranque
  por repetición y ningún watchdog en el log serie de r1. Ahí el puerto del broker rechaza la
  conexión al instante.
- **Hipótesis refinada:** el bucle aparece cuando el **host** del broker es inalcanzable (sin red, o
  con Wi-Fi a otra red), no cuando el host responde y el broker está caído. Una conexión TCP/TLS
  bloqueante que espera su tiempo límite dejaría sin atender al watchdog (que también vigila la
  tarea en reposo del núcleo 0, `idle_core_mask = 1<<0`). Falta confirmarlo con el log serie del
  momento del disparo, que nombra la tarea.
- **Para el paper:** reportarlo como defecto encontrado en banco, con su efecto nulo sobre la
  integridad.

**H2. Lecturas sin hora de captura cuando un arranque nunca sincroniza.**
- **Comportamiento normal:**
  - **Primera lectura de cada arranque:** sale con `time_quality = unsynced`, y el backend la
    guarda con la hora de **llegada** en `timestamp`. Si hay estimación, la pone en
    `timestamp_estimado`.
  - **Resto de lecturas:** en un reinicio por watchdog el reloj del ESP32 se conserva, así que
    las demás salen `synced`.
- **El problema:** en el arranque 951, que duró unos 70 min sin red y sin sincronizar, las 419
  lecturas quedaron `unsynced` **sin estimación**. Por eso aparecen en el panel con la hora de
  llegada: 70 min de curva quedan comprimidos en ≈ 1 min.
- **Análisis:** el script usa `COALESCE(timestamp_estimado, timestamp)` como hora de captura.
  Excluye las lecturas sin hora ni estimación y las lista en `sin_hora.csv`.
- **Para el paper:** es una limitación de la trazabilidad temporal. Esas lecturas se podrían
  reconstruir con `uptime_ms` y los arranques vecinos.

**H3. Reconstrucción de la hora tras un reset sin red (positivo).**
- **Qué se vio (S5 r1):** después del reset y sin red, las 23 lecturas del nuevo arranque llegaron
  como `backfilled`. La primera cae 3 s después de la última del arranque anterior, así que la
  reconstrucción es coherente.

**H5. La inyección de falla `*_una_vez` no funcionaba en el banco Docker (corregido para el ensayo).**
- **Qué pasaba:** el backend corre como usuario 10001 y no podía escribir su marca en el volumen
  `/pruebas`, que es de root (`PermissionError`). En vez de fallar una vez, fallaba en cada lectura.
- **Arreglo:** se dio permiso con `chmod 1777 /pruebas`, y lo hace `paper_s6_base.sh`.
- **Pendiente:** corregirlo en `docker-compose.pruebas.yml`.

**H6. Línea base sin acuse de aplicación (estimada).**
- **Qué se hizo:** en cada repetición, durante 120 s cada COMMIT falla con el backend vivo.
- **Cómo respondió el diseño evaluado:** el nodo espera el acuse y **reenvía la misma lectura**
  (5 reenvíos por repetición), y las capturadas mientras tanto esperan en el buffer.
- **Resultado:** las 12–13 lecturas expuestas por repetición quedaron todas guardadas, 0 perdidas.
- **Estimación:** un diseño que borra con el PUBACK habría publicado cada una una vez y las habría
  perdido, unas 12 por repetición.

**H4. La IP del hotspot puede cambiar entre sesiones del teléfono.**
- **Qué se vio:** de 10.123.44.175 pasó a 10.68.89.175. Hubo que agregar la IP nueva al
  certificado del broker, con la misma CA, y apuntar el nodo a ella.
- **Dentro de una sesión:** se mantuvo estable en todos los cortes de S2 y S5.

**H7. Tiempos de la puerta acotados por el ciclo de 10 s (S9).**
- **Cómo se mide la puerta:**
  - **Muestreo:** el nodo muestrea la puerta dentro del ciclo. La lectura informa
    `puerta_abierta = t` si estuvo abierta en algún momento del ciclo, más la racha continua
    (`door_open_streak_s`) y los segundos abiertos en el ciclo.
  - **Exactitud:** la duración de la apertura que mide el nodo coincide con la marcada a ±2 s. Ese
    margen cabe en la reacción de la operadora.
- **Qué se vio:**
  - **Detección de la apertura y del cierre:** se confirmaron 7,8–7,9 s después de la marca.
  - **Estado prolongado:** se confirmó a los 67,8–67,9 s.
- **Por qué los tiempos son tan constantes:** las marcas cayeron siempre en el segundo :00 y el
  nodo captura en el :07 de cada ciclo, así que el desfase fue el mismo en las 5 repeticiones.
- **En general:**
  - **Apertura o cierre:** se detecta entre 0 y 10 s después de ocurrir (intervalo) + latencia
    (≈ 0,9 s).
  - **Estado prolongado:** se ve entre 60 y 70 s + latencia.
- **Para el paper:** reportar la cota (≤ 10 s + latencia y ≤ 70 s + latencia) junto con lo medido.

**H8. `abierto_en` de `sin_datos` es nominal; la detección real tarda 30–60 s.**
- **Qué guarda el backend:** al abrir el incidente fija `abierto_en = último reporte + umbral (30 s)`.
  La tarea que vigila el silencio corre cada 30 s, así que el instante real en que el incidente
  existe (y se notifica) es `created_at`, entre 0 y 30 s más tarde.
- **Consecuencia para las cifras:** las de "sin_datos a los ≈ 30 s" que se calcularon antes de este
  hallazgo eran nominales.
- **Lo que se reporta ahora:** las corridas se reexportaron con `detectado_utc`
  (`paper_corrida.py reexportar-incidentes`), y la tabla informa las dos cifras.
- **Medido en las 35 corridas:** la detección real estuvo **entre 30,1 y 59,9 s** tras el último
  reporte, dentro de la cota de diseño (umbral + periodo de la tarea = 30–60 s).
- **Para el paper:** reportar la detección real (30–60 s) y no `abierto_en`. Opcional para Diego:
  el panel podría mostrar `created_at` como hora de detección, o bien la tarea podría correr cada
  10 s para acotar la detección a 30–40 s.

**H9. Desconexiones MQTT espontáneas en la red doméstica, sin pérdida (S1).**
- **Qué se vio:** 89 eventos LWT `DISPOSITIVO_OFFLINE` en 12 h, todos entre las 15:00 y las 19:00
  hora de Lima (12, 31, 40 y 6 por hora). Ninguno en las 8 h siguientes.
- **Efecto:** solo 15 duraron lo bastante para dejar lecturas en el buffer (31–46 s, 25 diferidas).
  El resto se reconectó dentro del ciclo de 10 s.
- **Resultado:** 0 faltantes, y los 32 reenvíos QoS 1 terminaron en 0 duplicadas. Esto prueba la
  idempotencia (S3 del paper, entrega duplicada) en condiciones reales y no solo provocadas.
- **Causa probable:** interferencia o carga del Wi-Fi doméstico por la tarde. No se investigó más.
- **Para el paper:** mencionarlo como evidencia de robustez. Las cifras de S1 incluyen esos cortes.

**H10. Fallas físicas del nodo el 7–8 nov.: SHT31 sin respuesta y reinicios por BROWNOUT (corregido).**
- **Qué se vio (7 nov., desde 15:36 hora de Lima):** el SHT31 respondía solo en la primera lectura de
  cada arranque y luego daba `sht31_fault: no_response`. El boot_id subió de 1063 a 1113 en pocas
  horas; los motivos reportados fueron sobre todo `BROWNOUT`, más algún `POWERON` y un `TASK_WDT`.
  Al presionar el SHT31 volvían los datos por momentos.
- **Diagnóstico:**
  1. Falso contacto en las soldaduras de SDA/SCL del módulo y cable de SCL suelto en C17
     (GPIO22). Con SCL abierto el arranque decía `Sin ACK en 0x44`.
  2. Tras resoldar SCL, el módulo seguía sin alimentación propia: 3,30 V en los cables puente pero
     0 V en los pines VIN/GND del SHT31. El sensor se alimentaba a medias por las pull-up de I2C y
     respondía la dirección pero no medía (`timeout`); en ese estado también fallaban la DS18B20
     (`Timeout de conversion`) y la lectura de la puerta.
  3. Resoldados VIN y GND (8 nov., 12:39), todo volvió: 30 min con 180/180 lecturas, SHT31 y
     DS18B20 sin fallas, 0 reinicios.
- **Reinicios por energía, aparte del SHT31:**
  - con la laptop **a batería** (plan "Silent") el USB no sostiene los picos del Wi-Fi: BROWNOUT;
  - el 8 nov. a las 13:38 un movimiento de la placa dejó al nodo en bucle de BROWNOUT (~195
    arranques, boot 1146–1339; el nodo reportó `perdidas: total=9`); se resolvió reconectando;
  - con la laptop **suspendida** (14:15–17:28) el USB se apaga y el nodo no captura nada; al
    despertar arranca con `BROWNOUT`.
- **Red:** el 8 nov. la laptop pasó a Ethernet (192.168.18.21, ya incluida en el certificado del
  broker) y el nodo se reapuntó a esa IP. Con la IP vieja el nodo se colgó intentando conectar y
  salió por `TASK_WDT` (mismo patrón que H1).
- **Verificación final (8 nov., 17:28–17:58, boot 1354):** 180/180 lecturas, seq sin huecos, SHT31
  y DS18B20 180/180, 0 reinicios, 2 desconexiones MQTT sin pérdida (latencia máx. 15,2 s).
- **Para las pruebas que faltan:** alimentar el nodo con el cargador de pared de 5 V, mantener la
  laptop despierta y con la tapa abierta, fijar los cables del SHT31 con cinta para que no tiren de
  las soldaduras, y usar siempre la misma conexión de la laptop (la IP es por DHCP).
- **No afecta a las corridas del 5–6 nov.:** S1 tuvo 0 reinicios y el SHT31 respondió en las
  4 321 lecturas.

## Suites de software (6 nov. 2026, 00:20–00:45 hora de Lima)

| Suite | Commit | Resultado | Cómo |
|---|---|---|---|
| Backend (pytest) | `4a8414d` | **1 164 pasadas, 0 fallidas, 0 omitidas** | Contenedor desechable con la imagen del backend (Python 3.13) más `requirements-dev.txt`, y un PostgreSQL 17 aparte (`TEST_POSTGRES_URL`), como en la CI. No toca la base de evidencia |
| Frontend (vitest 5.0.1) | `c306819` | **348 pasadas en 41 archivos** | `npx vitest run` |
| Firmware (pruebas nativas de `src/core`) | `c8c0cac` | **134 pasadas, 0 fallidas** | `scripts/tests_nativos_msvc.cmd` (MSVC 2022), con la Unity de framework-espidf |
| End to end (Playwright 1.63.0, Chromium) | `c306819` | **26 pasadas, 0 fallidas, 5 omitidas** (31 en 7 archivos; 1.1 min) | `npx playwright test` contra la demo (`npm run dev:demo`, 127.0.0.1:5173), corrida el 6 nov. 2026 a las 02:40 hora de Lima. Las 5 omitidas son la regresión visual de `visual.spec.ts`, que solo corre con `VISUAL=1` |

Las salidas están en `evidencia/paper/suites/`. En el paper (sección 5.1) estas cifras reemplazan a
"998 backend, 318 frontend and 26 end-to-end … 134 firmware": 1,164 backend, 348 frontend, 26 end-to-end y 134 firmware, todas al 6 nov. 2026.

