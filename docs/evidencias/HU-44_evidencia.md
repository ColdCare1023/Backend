# Evidencia — HU-44: rotación y revocación de credenciales del nodo

**Estado: verificado en pila local, modo manual simulado (pruebas A a G, 28/10/2026; F con un defecto
abierto). Pendiente: EMQX Cloud Serverless real.**
Todavía no cuenta como «verificada» (DoD): falta la prueba H.

Diseño y criterio: `docs/operacion/HU-44_rotacion_y_revocacion.md`. Firmware: IoT ADR-17.

## Entorno de las pruebas A y B (27/10/2026)

Registro completo, con los logs serie, la bitácora y las consultas:
IoT `docs/evidencias/HU-44_rotacion_relevo_evidencia.md`.

| Elemento | Valor |
|---|---|
| Broker | EMQX 5.8 local (`infra-local`), autenticación integrada y TLS 8883. La «consola» se simuló con la API de administración del EMQX local (`PUT /authentication/.../users/{id}`) |
| Backend | `688a0e2` (`develop`), `BROKER_USUARIOS_MODO=manual` |
| Nodo | ESP32 DevKitC V4 `ESP32_FARMACIA_01`, firmware `esp32dev` en `a220a80`, `intervalo_s = 10` |

> Las pruebas A y B son **anteriores** a los cambios de backend `252dfc2` y de firmware `1aea6bd` y
> `83d8f78`. Con esos cambios, la retención por compromiso ya no termina al confirmar la consola: termina
> con la prueba de posesión. Por eso hay que repetir la prueba B (B') con el firmware y el backend nuevos.

## Prueba A — rotación preventiva (informada por el equipo)

1. Se rota la credencial con causa `preventiva` y se carga el relevo en el nodo
   (`nvs set mqtt_relevo`).
2. Mientras la consola no cambia, el broker rechaza el token nuevo con
   **CONNACK 4** y el nodo sigue con el anterior.
3. Se cambia la contraseña en el broker y se confirma en el sistema. El nodo
   reconecta en **0,5 s** sin perder lecturas.

## Prueba B — rotación por compromiso (informada por el equipo)

1. Durante la ventana **no se guardó ninguna lectura**.
2. Al cerrarla se entregaron **28 de 28** lecturas: `seq_no` 60 a 87 del
   arranque `boot_id` 687, con su hora de captura original y sin huecos ni
   duplicados. Esto demuestra P1 y P2 en un nodo físico.
3. La bitácora de retenciones crece unas 60 filas por hora como máximo, porque
   el reintento del nodo va de 5 a 60 s.
4. **SIN_DATOS** se abrió a los 30 s, que son 3 × 10 s del intervalo de prueba.
   En el piloto, a 30 s, serían 90 s. Con el cambio `252dfc2` ya no se abre
   SIN_DATOS durante la retención: se abre «monitoreo_suspendido», que avisa con su propio texto (era la
   observación de la sección 7 de la evidencia de IoT).
5. Cada rotación deja en la conectividad el par `lwt_offline`/`online` del LWT.
   Es lo esperado. Desde `61670f8`, el backend anota cada expulsión de sesión
   que provoca y, durante los 120 s siguientes, audita ese par con
   `motivo: reconexion_por_rotacion`. Tras confirmar la consola, el motivo es
   `reconexion_por_confirmacion_consola`, y tras revocar,
   `expulsion_por_revocacion`.

Consulta para la tabla de lecturas recuperadas:

```sql
SELECT boot_id, seq_no, timestamp AS captured_at, received_at
FROM thermal_readings
WHERE device_id = '<DEVICE_ID>' AND boot_id = 687 AND seq_no BETWEEN 60 AND 87
ORDER BY seq_no;
-- Sin huecos: COUNT(*) = 28 y MAX(seq_no) - MIN(seq_no) + 1 = 28.
```

## Pruebas automáticas del backend (28/10/2026, commit `5926ed1`)

Corren en el CI del backend contra PostgreSQL. Ejercitan la lógica; no
reemplazan el banco.

| Archivo | Qué verifica | Resultado |
|---|---|---|
| `tests/integration/test_hu44_prueba_posesion.py` | Monitoreo suspendido abierto y avisado; SIN_DATOS no se abre en la retención; **C**: excursión en lectura retenida y en `lectura_vigente` (un solo episodio y un solo aviso; un sensor en falla no la dispara); prueba válida termina la retención y el nodo entrega lo retenido; **E**: confirmar sin cambiar la consola (firma con el token viejo) no sale de la retención; **D**: nonce inventado y prueba antes de confirmar se rechazan; nonce caducado; sesión nueva recibe **el mismo** nonce y uno a menos de 1 min de caducar se renueva; la respuesta al primer desafío sirve tras un reenvío; respuestas falsas en bucle dejan una fila por minuto y un solo aviso SSE, y lo suprimido se audita (`RETENCION_INTENTOS_SUPRIMIDOS`) al verificar, al revocar, al rotar de nuevo y, si el atacante deja de publicar, cuando vence su ventana (vigilante); «Reenviar desafío» expulsa la sesión con su motivo; la excursión guarda la lectura más reciente por hora de captura; el par LWT/online de una rotación se etiqueta | 22 pasan |
| `tests/unit/test_broker_admin.py` | Expulsión: un 404 solo cuenta como «no había sesión» con `CLIENTID_NOT_FOUND` (una ruta equivocada daba B', E y F por buenas); `GET /clients` al arrancar comprueba `BROKER_API_URL` | 19 pasan |
| `tests/integration/test_banco_dispositivo_script.py` | Script de banco (ADR-18): alta demo con el token en un archivo 0600 que no se imprime; `token` y `baja` no tocan un equipo real | 3 pasan |
| `tests/integration/test_hu44_rotacion_por_compromiso.py` | Rotación preventiva sin corte; retención sin acuse; eventos retenidos; confirmar expulsa la sesión y **sigue** reteniendo; modo API sin ventana | 6 pasan |
| `tests/integration/test_hu44_hu10_credenciales_dispositivo.py` | Revocación y rechazo de telemetría | pasan |
| IoT `test/test_core/test_posesion.cpp` | HMAC-SHA256 (RFC 4231) y la misma firma que verifica el backend | 6 pasan |
| IoT `test/test_core/test_llenado_banco.cpp` | Llenado rápido del buffer (ADR-18): tandas sin pasar de la capacidad; sintéticos marcados `simulated` y a 5,00 °C; `buffer cancelar` a mitad de una tanda deja el contador en 0 | 8 pasan |
| IoT `test/test_core/test_cola.cpp` (descarte) | `buffer descartar`: por tandas, sin tocar lo capturado después ni la cuarentena | 2 pasan |

Suite completa: **1041** pruebas del backend y **131** nativas del firmware.

## Pruebas de banco A a G (28/10/2026, pila local)

Backend `62082c9` en modo manual, firmware `esp32dev` `0fb14e8`, EMQX 5.8.6,
`ESP32_FARMACIA_01` arranque 697. Registro completo (logs serie, bitácora,
EMQX y consultas): IoT `docs/evidencias/HU-44_banco_A-G_evidencia.md`. Anexos:
`docs/evidencias/logs/2026-10-28_hu44_banco_serie.log` (log serie, tokens
sustituidos) y `docs/evidencias/logs/2026-10-28_hu44_banco_bitacora.csv`.

| Prueba | Resultado | Dato medido |
|---|---|---|
| **A** | PASS | Relevo rechazado con CONNACK 4; al confirmar, el nodo entra con el relevo en 0,45 s (`CREDENCIAL_ROTADA origen: relevo`) |
| **B'** | PASS | Prueba de posesión verificada **8,6 s** después de confirmar; 19/19 lecturas retenidas (`seq_no` 25–43) con su hora de captura; `monitoreo_suspendido` 181,7 s y sin SIN_DATOS |
| **C** | PASS (sin correo: SMTP no configurado) | `excursion_no_verificada` con `captura_a_alerta_s = 1.0`; un episodio por ventana |
| **D** | PASS | En la ventana: CONNACK 0 y PUBACK, sin acuse lógico, 0 lecturas persistidas, expulsión mutua. Tras la consola: CONNACK 134 |
| **E** | PASS | `PRUEBA_POSESION_FALLIDA firma_invalida`, una fila por minuto; tras cambiar la consola, «Reenviar desafío» verifica en 8,6 s; 14/14 lecturas |
| **F** | **PASS con defecto** | El broker corta al nodo (CONNACK 5 tras borrar el usuario), pero el nodo **no recibe** el acuse `rejected`: el backend expulsa la sesión antes de publicarlo. Bucle de reconexión cada 5 s: 35 `EVENTO_CREDENCIAL_REVOCADA` y 10 `LECTURA_RECHAZADA_CREDENCIAL_REVOCADA` en 83 s, sin limitador |
| **G** | PASS | `bad_username_or_password` (CONNACK 134) y `authorization_permission_denied` en `farmacias/FARM-02-TEST/lecturas`; 0 lecturas de FARM-02-TEST |

Arranque 697 completo: 90/90 lecturas (`seq_no` 1–90), sin huecos ni
duplicados.

Defectos abiertos:

1. `main.py`, rama `DispositivoNoAutorizadoError`: publicar el acuse
   `dispositivo_no_autorizado` **antes** de `_expulsar_sesion_revocada`,
   expulsar una sola vez y limitar sus filas de auditoría con `LIMITE_AUDITORIA`.
   Repetir F después.
2. `scripts/pruebas_hu44_banco.py`: el login va como JSON (422; la API espera
   formulario OAuth2), falta `WindowsSelectorEventLoopPolicy` para aiomqtt en
   Windows, la salida falla con cp1252 («→», «…») y `confirmar` repite las filas
   en cada consulta.

## Plan de las pruebas de banco (pila local)

El script `scripts/pruebas_hu44_banco.py` ejecuta los pasos que no hace el
nodo y deja la hora de cada resultado:

| Subcomando | Prueba |
|---|---|
| `atacante` | D |
| `token-invalido` y `acl` | G |
| `confirmar` | B' y E |
| `revocar` | F |
| `informe` | Bitácora en CSV para `docs/evidencias/logs/` |

Los tokens se leen de variables de entorno, nunca de los argumentos. El script
se ejecutó contra la pila el 28/10/2026 (resultados arriba), con los arreglos
locales del defecto 2.

Para cada prueba se registra lo siguiente:

- fecha;
- commits de firmware y backend;
- log serie del nodo;
- `docker compose logs emqx`;
- las filas de `audit_logs` e `incidentes_operativos`.

| Prueba | Cómo se induce | Qué se espera | Qué se mide |
|---|---|---|---|
| **B'** (repetir B) | Rotación por compromiso, cambiar la consola y confirmar | `CREDENCIAL_POSESION_VERIFICADA`; el nodo entrega lo retenido | Tiempo desde la confirmación hasta la prueba; lecturas recuperadas |
| **C** | Sonda en hielo o junto a una fuente de calor durante la ventana | Incidente `excursion_no_verificada` y su correo | `captura_a_alerta_s` en la auditoría |
| **D** | Segundo cliente MQTT con el token viejo, durante la ventana y después de confirmar | Nada se persiste en la ventana. Después: CONNACK 4. La expulsión mutua queda documentada | Filas `LECTURA_RETENIDA_*` y CONNACK del cliente |
| **E** | Confirmar sin cambiar la consola | El nodo vuelve con el token viejo; `PRUEBA_POSESION_FALLIDA` con `firma_invalida`; la retención sigue | Auditoría y estado `posesion_pendiente` |
| **F** | Revocar | Token revocado rechazado; telemetría con acuse `rejected` y en cuarentena en el nodo | `LECTURA_RECHAZADA_CREDENCIAL_REVOCADA`; `cuarentena` en `estado_nodo` |
| **G** | Token inválido y publicación en el tópico de otro nodo | Escenarios 2 y 3 de HU-10 (ver `HU-10_evidencia.md`) | CONNACK y `authorization_permission_denied` en EMQX |
| **H** | Repetir A a G contra **EMQX Cloud Serverless** | Lo que exige la DoD | Igual que arriba. La API local simula la consola, pero no la reemplaza |

## Anexo 5

En el Anexo 5, HU-44 debe figurar como «Implementado por verificar», con la
anotación «verificado en pila local, modo manual simulado; pendiente Serverless
real».
