# HU-44: rotación y revocación con EMQX Serverless (modo manual)

Con `BROKER_USUARIOS_MODO=manual` el usuario del nodo se crea, cambia o retira a
mano en la consola de EMQX. Entre la operación en el sistema y la edición de la
consola hay una **ventana** en la que el broker todavía acepta el token
anterior. El backend no sabe con qué token se autenticó cada conexión, así que
cierra esa ventana a nivel de aplicación.

## Comportamiento

| Operación | Mientras la consola está pendiente | Al confirmar la consola |
|---|---|---|
| Revocar | La telemetría y los eventos se rechazan, se auditan (`LECTURA_RECHAZADA_CREDENCIAL_REVOCADA`) y se expulsa la sesión en cada intento. El rechazo es **permanente** (con acuse): el nodo libera esas lecturas. | Se expulsa la sesión. |
| Rotar, causa `preventiva` | La ingesta sigue aceptando al nodo (el token anterior no está comprometido). | Se expulsa la sesión: el nodo reconecta con el token de relevo (IoT ADR-17). |
| Rotar, causa `compromiso` | La telemetría y los eventos se **retienen**: no se persisten, se auditan (`LECTURA_RETENIDA_ROTACION_COMPROMISO`, `EVENTO_RETENIDO_ROTACION_COMPROMISO`) y **no llevan acuse**, así que el nodo legítimo los conserva en LittleFS. El dispositivo muestra `rotacion_por_compromiso = true`. | Se limpia la marca y se expulsa la sesión (cambiar la contraseña no cierra las sesiones abiertas). El nodo entra con el relevo y reenvía lo retenido. |

En modo `api` (EMQX dedicado o local) no hay ventana: el broker cambia la
contraseña y se expulsa la sesión en la misma operación.

Durante la retención no llegan lecturas ni latidos, así que el incidente
SIN_DATOS (HU-54) se abre tras N intervalos. Es lo esperado: en esa ventana el
sistema no tiene telemetría en la que confiar. La capacidad del buffer
(30 000 lecturas, unos 10 días a 30 s) cubre de sobra una ventana de horas.

Límite conocido: mientras el broker acepte el token filtrado, el nodo y quien
lo tenga comparten `client_id` y se expulsan entre sí. Solo lo resuelve editar
la consola; por eso la confirmación es el paso que cierra la operación.

## Criterio de aceptación propuesto (reemplaza el Escenario 2 de HU-44)

> **Dado** un nodo cuyo token se sospecha comprometido y un broker en modo manual,
> **cuando** el administrador rota la credencial con causa «compromiso»,
> **entonces** el sistema emite el token nuevo una sola vez, deja la operación
> «pendiente en el broker» con fecha y responsable, y **desde ese instante y hasta
> que el administrador confirme la consola** no persiste telemetría ni eventos del
> nodo, audita cada intento y no envía acuse;
> **y cuando** el administrador confirma la consola, el sistema expulsa la sesión
> abierta, vuelve a aceptar la telemetría y el nodo entrega lo que retuvo, sin
> pérdida mientras no se supere la capacidad del buffer.

## Firmware (IoT ADR-17)

Con un token de relevo cargado, el nodo lo prueba **primero** en cada conexión.
Si el broker lo acepta, lo promueve a token principal y **borra el anterior** en
ese mismo momento. Si lo rechaza (la consola aún no cambió), entra con el
anterior y vuelve a probar el relevo en la siguiente reconexión. El relevo nunca
queda como respaldo permanente.
