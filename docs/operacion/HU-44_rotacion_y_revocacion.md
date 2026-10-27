# HU-44: rotación y revocación con EMQX Serverless (modo manual)

Con `BROKER_USUARIOS_MODO=manual` el usuario del nodo se crea, cambia o retira a
mano en la consola de EMQX. Entre la operación en el sistema y la edición de la
consola hay una **ventana** en la que el broker todavía acepta el token
anterior. El backend no sabe con qué token se autenticó cada conexión, así que
cierra esa ventana a nivel de aplicación.

## Comportamiento

| Operación | Mientras dura la ventana | Cómo termina |
|---|---|---|
| Revocar | La telemetría y los eventos se rechazan y se auditan (`LECTURA_RECHAZADA_CREDENCIAL_REVOCADA`), y la sesión se expulsa en cada intento. El rechazo es **permanente** (con acuse `rejected`): el nodo pasa esas lecturas a cuarentena en LittleFS (no las borra; quedan contadas). | Se confirma la consola y se expulsa la sesión. |
| Rotar, causa `preventiva` | La ingesta sigue aceptando al nodo, porque el token anterior no está comprometido. | Se confirma la consola y se expulsa la sesión: el nodo reconecta con el token de relevo (IoT ADR-17). |
| Rotar, causa `compromiso` | La telemetría y los eventos se **retienen**: no se persisten, se auditan (`LECTURA_RETENIDA_ROTACION_COMPROMISO`, `EVENTO_RETENIDO_ROTACION_COMPROMISO`) y **no llevan acuse**, así que el nodo legítimo los conserva en LittleFS. Se abre el incidente **«monitoreo_suspendido»** y se avisa; SIN_DATOS no se abre. Las lecturas retenidas y las muestras `lectura_vigente` se evalúan con la regla 2-8 °C (ver abajo). | **Prueba de posesión** (ver abajo). Confirmar la consola no basta. |

En modo `api` (EMQX dedicado o local) no hay ventana: el broker cambia la
contraseña y la sesión se expulsa en la misma operación.

## Vigilancia térmica durante la retención

Si el refrigerador sale de 2-8 °C durante la ventana, la alerta no espera a que
termine la retención.

- El drenaje FIFO del nodo solo reintenta la lectura más antigua mientras no hay
  acuse. Por eso el nodo publica además `lectura_vigente` con su última captura,
  una vez por lectura (IoT, contrato v1.0).
- Tanto la lectura retenida como la muestra vigente se evalúan con la misma
  regla que la ingesta normal (P3). Una temperatura válida fuera de rango abre el
  incidente **«excursion_no_verificada»**:
  - crítico y con origen no verificado, porque puede venir de quien tiene el
    token filtrado;
  - se avisa por correo y, si hay SMS, como una excursión crítica;
  - la auditoría guarda el tiempo entre la captura y la alerta
    (`captura_a_alerta_s`).
- Un sensor en falla (`null`) nunca dispara la regla.
- Es **un episodio por ventana**: no se cierra aunque la temperatura vuelva a
  rango, porque no hay forma de saber si el dato viene del nodo. Su `detalle`
  conserva la lectura que lo abrió y, además, la más reciente por hora de
  captura (`ultima_temperatura_interna`, `ultima_captura` y `ultima_en_rango`).
  Una lectura retenida que el nodo reintenta desde su cola es anterior a la
  muestra vigente y no la reemplaza.
- El SMS también lo marca como «ORIGEN NO VERIFICADO».
- El incidente no se persiste como lectura ni entra en la cadena de lecturas.
  Cuando el nodo prueba la posesión, entrega lo retenido con su hora de captura
  original y la regla abre entonces la alerta térmica normal.

## Prueba de posesión

En Serverless la secuencia es manual: primero se cambia la contraseña en la
consola y después se confirma en el sistema. Si el administrador confirma
**antes** de cambiarla, o el cambio no se guarda, ocurre lo siguiente:

1. El backend expulsa la sesión.
2. El nodo prueba el relevo, el broker lo rechaza y el nodo vuelve con el token
   viejo.
3. El broker sigue aceptando ese token comprometido, también para el atacante.

Por eso la confirmación **no** termina la retención. Termina cuando el nodo
demuestra que su sesión usa el token nuevo:

1. Al rotar, el backend guarda `pop_clave = SHA-256("coldcare-pop-v1:" + token)`.
   Guarda esa clave derivada, nunca el token.
2. Al confirmar la consola, el backend expulsa la sesión y publica en
   `farmacias/<id>/ack` el mensaje
   `{"tipo":"desafio_posesion","nonce":...}`.
   - El nonce es de 128 bits, caduca a los 10 min y sirve una sola vez.
   - Si el nodo no lo recibe porque estaba reconectando, la ingesta lo reenvía
     con el siguiente mensaje retenido (como mucho cada 30 s) o enseguida tras
     el `online` de una sesión nueva.
   - Mientras le quede más de 1 min de vigencia, se reenvía **el mismo**
     nonce. Así una respuesta al desafío de la API que llegue después del
     reenvío de la ingesta no se audita como fallo. Con menos de 1 min se
     emite uno nuevo, para que el nodo no responda uno ya caducado.
3. El nodo responde con el evento `prueba_posesion` y
   `hmac = HMAC-SHA256(pop_clave, nonce + ":" + device_id)`, calculado con el
   token de **su sesión**.
4. Si la firma coincide, se audita `CREDENCIAL_POSESION_VERIFICADA` y se
   resuelven los dos incidentes. El nodo vuelve a recibir acuses y entrega lo
   retenido.
5. Si no coincide (nodo con el token viejo, atacante, nonce inventado o
   caducado), se audita `PRUEBA_POSESION_FALLIDA` con su motivo y la retención
   sigue:
   - `firma_invalida`
   - `nonce_desconocido`
   - `nonce_caducado`
   - `consola_pendiente`

### Si se confirmó sin cambiar la consola (caso E)

En ese caso la retención sigue y la bitácora muestra `PRUEBA_POSESION_FALLIDA`
con motivo `firma_invalida`. La operación ya no está pendiente
(`broker_pendiente` es nulo), así que no se puede confirmar otra vez. Para
salir:

1. Cambie la contraseña del usuario `<device_id>` en la consola de EMQX.
2. Use **«Reenviar desafío»** en Dispositivos
   (`POST /api/dispositivos/{id}/credenciales/reenviar-desafio`). El backend
   expulsa la sesión, el nodo vuelve a probar el relevo, el broker ya lo acepta
   y el nodo responde el desafío con el token nuevo. Queda auditado como
   `DESAFIO_POSESION_REENVIADO`.

Sin ese botón, la salida depende del sondeo del relevo que hace el nodo cada
5 min (IoT ADR-17) o de un reinicio. En la prueba E se mide cuánto tarda
cada camino.

### Tope de la bitácora durante la retención

Quien tiene el token filtrado puede publicar en bucle lecturas, eventos o
respuestas falsas en los tópicos del nodo. Por eso se audita como mucho una
fila por minuto para cada combinación de dispositivo, tipo de registro y motivo
(`LECTURA_RETENIDA_*`, `EVENTO_RETENIDO_*`, `PRUEBA_POSESION_FALLIDA`). Cada
fila lleva `suprimidos_desde_anterior`, con la cantidad de intentos que solo se
contaron. El contador vive en memoria: el backend corre con una réplica y un
worker.

- El aviso en tiempo real (SSE `prueba_posesion`) de un fallo sigue el mismo
  tope; el de la verificación sale siempre.
- Al verificar la posesión, lo contado desde la última fila de cada clave queda
  en una fila de cierre `RETENCION_INTENTOS_SUPRIMIDOS`
  (`suprimidos_sin_auditar`, p. ej. `{"pop_fallida:firma_invalida": 3}`).
  Tras un reinicio del backend ese resto se pierde.

Un evento `credencial_rotada origen=relevo` no sirve como prueba: lo puede
publicar cualquiera que tenga el token viejo en el tópico del dispositivo.
Quien solo tiene el token filtrado no conoce `pop_clave` y no puede firmar.

El dispositivo expone `rotacion_por_compromiso` y `posesion_pendiente`. Este
último indica que la consola está confirmada y falta la prueba. El estado
operativo del nodo es `MONITOREO_SUSPENDIDO`.

## Bitácora de conectividad

Cada expulsión de sesión que hace el backend (al rotar, al confirmar la
consola, al reenviar el desafío o al revocar) queda anotada con su hora y su
motivo. Durante los 120 s siguientes, el LWT y el `online` que produce esa
expulsión se auditan con `motivo` = `reconexion_por_rotacion`,
`reconexion_por_confirmacion_consola`, `reconexion_por_reenvio_desafio` o
`expulsion_por_revocacion`, y así no se confunden con una caída del nodo.

## Límites conocidos

- **Expulsión mutua.** Mientras el broker acepte el token filtrado, el nodo y
  quien lo tenga comparten `client_id` y se expulsan entre sí. Solo lo resuelve
  editar la consola.
- **Firmware anterior.** Un nodo sin soporte para la prueba de posesión
  (firmware anterior a `1aea6bd`) no puede salir de la retención por
  compromiso. Antes de usar esa causa, actualice el firmware; en su lugar, use
  una revocación seguida de un alta nueva.
- **Fuga de la base de datos.** Quien lea la base de datos obtiene `pop_clave`.
  Con ella y el token viejo podría firmar el desafío. Es un nivel de acceso
  mayor que el del escenario de HU-44, que es un token filtrado.
- **Capacidad del buffer.** Las 30 000 lecturas (unos 10 días a 30 s) cubren de
  sobra una ventana de horas.

## Criterio de aceptación propuesto (Escenario 2 de HU-44)

> **Dado** un nodo cuyo token se sospecha comprometido y un broker en modo manual,
> **cuando** el administrador rota la credencial con causa «compromiso»,
> **entonces** el sistema emite el token nuevo una sola vez, deja la operación
> «pendiente en el broker» con fecha y responsable, abre el incidente «monitoreo
> suspendido» y lo avisa, y **desde ese instante hasta que el nodo pruebe la
> posesión del token nuevo** no persiste telemetría ni eventos del nodo, audita
> cada intento y no envía acuse;
> **y si** durante la ventana llega una temperatura válida fuera de 2-8 °C,
> **entonces** abre una excursión crítica de origen no verificado y la avisa;
> **y cuando** el administrador confirma la consola, el sistema expulsa la
> sesión y envía un desafío; solo una respuesta firmada con el token nuevo
> termina la retención, y entonces el nodo entrega lo que retuvo sin pérdida
> mientras no se supere la capacidad del buffer;
> **y si** el administrador confirmó sin cambiar la consola, la retención sigue
> y la respuesta inválida queda auditada.

## Firmware (IoT ADR-17)

- **Token de relevo.** Con un relevo cargado, el nodo lo prueba **primero** en
  cada conexión.
  - Si el broker lo acepta, el nodo lo promueve a token principal y **borra el
    anterior** en ese mismo momento.
  - Si lo rechaza porque la consola aún no cambió, el nodo entra con el anterior
    y vuelve a probar el relevo en la siguiente reconexión.
  - El relevo nunca queda como respaldo permanente.
- **Desafío y muestra vigente.** El nodo responde el desafío con el token de su
  sesión (`core::respuestaPosesion`). Mientras el drenaje está detenido, publica
  `lectura_vigente`.
