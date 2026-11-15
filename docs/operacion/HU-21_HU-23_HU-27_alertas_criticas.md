# Alertas críticas: estados, reapertura, permisos y enlaces (HU-21, HU-23, HU-27, HU-52, HU-54)

Cierra los P0-1, P0-2, P1-8 y P1-9 (H16) de `VALIDACION_FINAL_BACKLOG.md`.

## Máquina de estados (HU-23 esc. 1 y 3, HU-27 esc. 1)

El flujo es siempre **PENDIENTE → RECONOCIDA → ATENDIDA**.

| Operación | Desde | Hacia | Qué queda registrado |
|---|---|---|---|
| `PATCH /api/alertas/{id}/revisar` (reconocer) | PENDIENTE | RECONOCIDA | `revisada_por`, `reconocida_en`; `REVISAR_ALERTA` en la bitácora y en la cadena del dispositivo |
| `POST /api/alertas/{id}/acciones-correctivas` sobre una RECONOCIDA | RECONOCIDA | ATENDIDA | `atendida_en`; eslabón `ACCION_CORRECTIVA` y `REGISTRAR_ACCION_CORRECTIVA` |
| `POST /api/alertas/{id}/acciones-correctivas` sobre una PENDIENTE | PENDIENTE | RECONOCIDA → ATENDIDA | Primero el reconocimiento de quien registra la acción (`revisada_por`, `reconocida_en`, `REVISAR_ALERTA` con `detalle.origen = "accion_correctiva"`), después la acción. En la cadena: `REVISAR_ALERTA` → `ACCION_CORRECTIVA` → `REGISTRAR_ACCION_CORRECTIVA` |
| Cualquier acción sobre una ATENDIDA | — | — | 409, no se registra nada (HU-27 esc. 2) |

- `marcar_atendida_si_no_atendida` solo transiciona desde RECONOCIDA (`UPDATE … WHERE estado = 'reconocida'`).
- El reconocimiento implícito también es atómico (`UPDATE … WHERE estado = 'pendiente'`). En PostgreSQL, dos QF que registran a la vez una acción sobre la misma alerta pendiente dan un 201 y un 409, con un solo reconocimiento y una sola acción (`test_postgres_acciones_simultaneas_sobre_una_pendiente`).

## Reapertura dentro de la ventana (HU-21 esc. 3, HU-20)

`ALERTA_VENTANA_NORMALIZACION_MINUTOS` (15 por defecto) reabre el último episodio cerrado del mismo dispositivo y nivel si la siguiente excursión llega dentro de la ventana. Así se evita el flapping. **Un episodio ATENDIDO nunca se reabre.** La nueva excursión crea una alerta PENDIENTE nueva, que aparece en `GET /api/alertas?estado=pendiente` y se notifica por correo. Los episodios PENDIENTE o RECONOCIDA conservan la histéresis.

## Permisos (H16, HU-27 y HU-41)

| Ruta | Roles explícitos | Notas |
|---|---|---|
| `PATCH /api/alertas/{id}/revisar` | farmacéutico | Sin cambios |
| `POST /api/alertas/{id}/acciones-correctivas` | **farmacéutico** (antes: farmacéutico y técnico) | El técnico recibe 403 (auditado como `RBAC_DENEGADO`) y la alerta no cambia |
| `POST /api/alertas/acciones-correctivas/{id}/rectificar` | farmacéutico, técnico | Sin cambios. El caso de uso solo permite rectificar al autor o a un farmacéutico, así que al técnico solo le quedan las acciones que registró antes de H16 |
| `GET /api/alertas`, `GET …/acciones-correctivas` | farmacéutico, técnico, auditor | Sin cambios |

El administrador tiene acceso implícito a toda ruta protegida (`rbac.verificar_permiso`). La matriz de `tests/integration/test_hu41_rbac_matriz.py` lista solo los roles explícitos.

## Enlaces de los correos (HU-52 y HU-54)

| Correo | Enlace |
|---|---|
| Excursión crítica y riesgo preventivo | `FRONTEND_URL/alertas?id=<alerta_id>` |
| SIN_DATOS, monitoreo suspendido, excursión no verificada, falla del sensor, colisión de identidad | `FRONTEND_URL/alertas` (rail de incidentes), con el texto «Ver el incidente en el panel de alertas». Antes llevaban a `/dispositivos?id=`, que solo abre el administrador |

## Pendiente fuera del backend

- Frontend: quitar `'tecnico'` de quien puede registrar la acción (`AlertasPage.tsx`) y leer `?id` con `useSearchParams` para abrir la alerta del correo.
- Producción: excursión inducida → reconocimiento o acción → recuperación → nueva excursión dentro de 15 min, comprobando la alerta pendiente nueva y el segundo correo (P1-13).
