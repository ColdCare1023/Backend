# HU-54: incidente SIN_DATOS

La tarea periódica (`src/interface/tareas.py`, cada `SIN_DATOS_PERIODO_SEGUNDOS`)
abre un incidente en `incidentes_operativos` cuando el silencio de un nodo activo
alcanza **N × intervalo de captura** y lo resuelve con la siguiente lectura.

| Parámetro | Valor de fábrica | Dónde se cambia |
|---|---|---|
| N | 3 | `SIN_DATOS_N_INTERVALOS` |
| Intervalo de captura | 30 s → umbral de 90 s | `estado_nodo.intervalo_s` del nodo, o `PATCH /api/dispositivos/{id}/intervalo` |
| Periodo de la tarea | 30 s | `SIN_DATOS_PERIODO_SEGUNDOS` |

## Antes del primer `estado_nodo`

Hasta que el nodo envía su primer `estado_nodo` (firmware anterior a v1.0, o
evento perdido), el backend no conoce el intervalo real. En ese caso usa
`devices.intervalo_reporte_segundos`, que vale **30 s** por defecto (columna con
`server_default` 30, migración 0030), es decir, un umbral de **90 s**. Es el
mismo valor que trae el firmware de fábrica (IoT `config.h`), así que un nodo
sin configurar coincide con el umbral.

Si el nodo se configuró con otro intervalo y aún no envió `estado_nodo`, el
administrador puede fijarlo con `PATCH /{id}/intervalo` para no abrir falsos
SIN_DATOS (intervalo más largo) ni detectarlos tarde (intervalo más corto). En
cuanto llega un `estado_nodo` con `intervalo_s`, ese valor reemplaza al
anterior.

Un nodo que nunca envió una lectura (recién dado de alta) no abre incidente: el
tablero lo muestra sin datos, pero no hay un último reporte desde el que medir.

## Retención por rotación por compromiso

Mientras una rotación por compromiso siga pendiente en el broker (HU-44), la
ingesta no acepta lecturas ni latidos del nodo, así que el incidente se abre
tras N intervalos. Es lo esperado: en esa ventana no hay telemetría en la que
confiar. Ver `HU-44_rotacion_y_revocacion.md`.
