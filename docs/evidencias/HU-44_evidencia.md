# Evidencia — HU-44: rotación y revocación de credenciales del nodo

**Estado: verificado en pila local, modo manual simulado. Pendiente: EMQX Cloud Serverless real.**
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

## Pruebas automáticas del backend (27/10/2026, commit `252dfc2`)

Corren en el CI del backend contra PostgreSQL. Ejercitan la lógica; no
reemplazan el banco.

| Archivo | Qué verifica | Resultado |
|---|---|---|
| `tests/integration/test_hu44_prueba_posesion.py` | Monitoreo suspendido abierto y avisado; SIN_DATOS no se abre en la retención; **C**: excursión en lectura retenida y en `lectura_vigente` (un solo episodio y un solo aviso; un sensor en falla no la dispara); prueba válida termina la retención y el nodo entrega lo retenido; **E**: confirmar sin cambiar la consola (firma con el token viejo) no sale de la retención; **D**: nonce inventado y prueba antes de confirmar se rechazan; nonce caducado; sesión nueva recibe desafío nuevo | 10 pasan |
| `tests/integration/test_hu44_rotacion_por_compromiso.py` | Rotación preventiva sin corte; retención sin acuse; eventos retenidos; confirmar expulsa la sesión y **sigue** reteniendo; modo API sin ventana | 6 pasan |
| `tests/integration/test_hu44_hu10_credenciales_dispositivo.py` | Revocación y rechazo de telemetría | pasan |
| IoT `test/test_core/test_posesion.cpp` | HMAC-SHA256 (RFC 4231) y la misma firma que verifica el backend | 6 pasan |

Suite completa: **1009** pruebas del backend y **121** nativas del firmware.

## Pruebas de banco que faltan (pila local)

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
todavía no se ha ejecutado contra la pila; sus pruebas unitarias solo comprueban
que la telemetría que publica cumple el contrato v1.0.

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
