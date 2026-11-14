# HU-31 / HU-36 / HU-48 / HU-07 / HU-35: serie agregada, buffer del nodo y hora de las lecturas

Cambios aditivos de la API: ningún campo existente cambia de nombre, tipo ni
significado. Migración `0040_estado_nodo` (solo crea dos tablas; el downgrade
las borra).

## Serie agregada: `GET /api/lecturas/serie` (HU-31, HU-36)

Roles: técnico, químico farmacéutico y auditor (los mismos que
`GET /api/lecturas`; el administrador tiene acceso implícito).

| Parámetro | Por defecto | Regla |
|---|---|---|
| `device_id` | obligatorio | patrón del contrato; 404 si no existe |
| `hasta` | ahora | — |
| `desde` | `hasta` − 24 h (HU-31 esc. 4) | `desde > hasta` → 422 (HU-36 esc. 3); periodo máximo 366 días |
| `bucket` | `auto` | `auto` elige 60 s, 120 s, 5, 10, 15, 30 min, 1, 2, 4, 6, 12 o 24 h para no pasar de 1500 intervalos (24 h → 1 min; 7 d → 10 min; 30 d → 30 min). Con un número: 10 a 86400 s y como mucho 5000 intervalos |

Respuesta (`SerieTermicaResponse`):

- `buckets[]`: `inicio`, `fin`, `lecturas` y, para `temperatura_interna`,
  `temperatura_ambiental` y `humedad_ambiental`, `{min, max, promedio, n}`;
  además `fuera_de_rango` (temperatura interna fuera de 2-8 °C) y
  `excursiones` (excursión confirmada). Los intervalos se alinean a múltiplos
  de `bucket_segundos` desde la época UNIX y los vacíos no se devuelven.
- `brechas[]`: `desde`, `hasta`, `duracion_segundos`, `causa`
  (`sin_datos` o `buffer_agotado` si coincide con un episodio de buffer
  saturado), `perdidas` y `abierta` (la brecha llega hasta ahora). El umbral es
  el de SIN_DATOS: N × intervalo del nodo (`umbral_brecha_segundos`). Tope de
  500 (`brechas_truncadas`).
- `resumen`: estadísticas de todo el periodo, `por_riesgo_efectivo`,
  `primera_lectura_en` y `ultima_lectura_en`.
- `total_lecturas`: lecturas del nodo con hora en el periodo (suma de los
  intervalos). `lecturas_sin_hora` y `lecturas_manuales` se cuentan aparte y no
  entran en la curva.

La hora de cada lectura en la serie es la de captura: `timestamp_estimado` si el
backend la reancló, `timestamp` si no.

### Total sin paginar en `GET /api/lecturas`

Toda respuesta trae la cabecera `X-Total-Count` con el total de filas para los
mismos filtros, sin `limite` ni `offset`. CORS la expone al frontend. Con ella
la tabla del Historial pagina con `offset` y avisa cuando lo mostrado no cubre
el periodo.

## Hora de cada lectura (HU-07)

`LecturaResponse` (REST y SSE) agrega:

| Campo | Significado |
|---|---|
| `sin_hora` | `true` si la lectura es de un arranque sin hora sincronizada que aún no se reancló: su `timestamp` es la hora de **llegada** al backend. Se rotula «sin hora» y no se ubica en la curva |
| `origen_hora` | `nodo`, `reconstruida` (`timestamp_estimado`), `recepcion` (sin hora) o `manual` |
| `recibida_en` | instante en que el backend recibió la lectura |

En cuanto llega una lectura sincronizada del mismo arranque, el backend reancla
las anteriores (`timestamp_estimado`) y `sin_hora` pasa a `false`. Un arranque
que nunca sincroniza sigue «sin hora».

## Estado operativo: `GET /api/dispositivos/estado-operativo` (HU-35, HU-48)

Campos nuevos por dispositivo:

- `umbral_puerta_abierta_segundos` (HU-35): `PUERTA_UMBRAL_PROLONGADA_SEGUNDOS`
  (60 s por defecto). Se informa aunque no haya lecturas: el tablero no debe
  usar un valor propio.
- `buffer_littlefs` (HU-48), `null` si el nodo nunca envió `estado_nodo` ni
  tuvo brechas:
  - `pendientes`, `limite` (capacidad informada o 30 000), `ocupacion_pct`,
    `cuarentena`, `reportado_en`.
  - `desde` / `hasta`: rango de los pendientes (`null` sin pendientes). Si el
    firmware no lo informa, se estima desde la última lectura persistida hasta
    el último latido y `rango_estimado = true`.
  - `perdidas`: contador persistente de muestras no almacenadas del nodo.
  - `ultima_sincronizacion_en`: último latido con 0 pendientes (HU-48 esc. 3).
  - `brechas[]` (10 más recientes): `desde`, `hasta`, `muestras_perdidas`
    (= `perdidas`), `causa = "buffer_agotado"`, `causa_nodo` (la del firmware,
    p. ej. `buffer_lleno`), `descripcion`, `abierta`, `registrada_en`,
    `actualizada_en`, `cerrada_en` y la identidad de la primera y la última
    muestra perdida (`desde_boot_id`, `desde_seq_no`, `hasta_boot_id`,
    `hasta_seq_no`).

### De dónde salen los datos

- `estado_nodo` (cada 5 min): el backend guarda el último en
  `estado_nodo_dispositivo` (además del latido de HU-54). Sigue sin auditarse
  y sigue saliendo por SSE.
- `buffer_saturado`: se audita como antes y, además, abre o actualiza un
  registro en `episodios_buffer_saturado`, identificado por la primera muestra
  perdida. El cierre (`episodio_abierto = false`) lo cierra; un episodio nuevo
  cierra el anterior si su cierre no llegó (p. ej. reinicio del nodo).
- Los eventos de un nodo con la credencial revocada o en rotación por
  compromiso no se registran (mismo criterio que el latido).

### Extensión pendiente del firmware

El backend ya acepta en el `detail` de `estado_nodo` dos claves opcionales, en
ISO 8601 UTC: `pendientes_desde` (captura del pendiente más antiguo) y
`pendientes_hasta` (del más nuevo). Mientras el firmware no las envíe, el rango
se estima (`rango_estimado = true`).

## Verificación pendiente en hardware

1. HU-48 esc. 1-3: desconectar el ESP32 del broker hasta acumular pendientes,
   comprobar `buffer_littlefs.pendientes` y el rango; reconectar y comprobar que
   `pendientes` vuelve a 0 y `ultima_sincronizacion_en` se actualiza.
2. HU-48 esc. 2 / HU-06 esc. 3: saturar LittleFS (llenado sintético o
   desconexión prolongada) y comprobar la brecha con `causa_nodo = buffer_lleno`,
   sus pérdidas y que `GET /api/lecturas/serie` marca el tramo como
   `buffer_agotado`.
3. HU-07: arrancar el nodo sin SNTP, comprobar `sin_hora = true` y, al
   sincronizar, `origen_hora = reconstruida`.
4. HU-31 / HU-36: con datos reales del despliegue, pedir la serie de 24 h y de
   7 días y comprobar que `resumen.primera_lectura_en` y `ultima_lectura_en`
   cubren el periodo y que el Historial recibe `X-Total-Count`.
