# Despliegue de la ronda 2 (backend, frontend y firmware)

Orden y comprobaciones para llevar a producción las correcciones de la ronda 2
de validación del backlog. Ningún paso cambia datos existentes; la única
migración es `0042_bigint_identidad` (reversible: su *downgrade* se niega a
truncar valores fuera de INTEGER).

## Orden

1. **Frontend antes o junto con el backend.** El backend r2 responde 403 al
   técnico en `POST /api/alertas/{id}/acciones-correctivas` (H16: la acción la
   registra el QF). El frontend de `main` anterior todavía le muestra el botón
   «Registrar acción»; el frontend r2 ya no. Si se despliega solo el backend, el
   técnico ve el botón y recibe el error 403.
2. **Backend.** El *pre-deploy* `alembic upgrade head` aplica 0042 (el `ALTER
   TYPE` bloquea `thermal_readings` unos segundos). Después, en
   `/health/detalle`: `alembic_revision == alembic_head` (si no, el *pre-deploy*
   no corrió), `db: ok`, `mqtt.conectado: true`.
3. **Firmware v1.2.1 después del backend.** El nodo publica
   `desconexion_ordenada` antes de un DISCONNECT voluntario (HU-13 esc. 2). El
   backend r2 lo acepta y lo audita como `DESCONEXION_ORDENADA`; un backend
   anterior lo descarta con un aviso en el log. Si hay que grabar el nodo antes,
   compile con `-DPUBLICAR_DESCONEXION_ORDENADA=0`.

## Variables nuevas o con otro valor por defecto (servicio de la API)

| Variable | Por defecto | Qué hacer |
|---|---|---|
| `RESPALDO_MAX_ANTIGUEDAD_HORAS` | `0` (apagado) | Poner `26` cuando exista el cron de respaldo (HU-29 esc. 3). |
| `RESPALDO_VIGILANCIA_PERIODO_SEGUNDOS` | `3600` | Nada. |
| `SSE_MAX_STREAMS_POR_USUARIO` | `10` (antes `3`) | Nada, salvo que el panel fije otro valor (HU-32 esc. 3). |

`IA_LEGADO_MODO=activo` ya no basta para que el modelo legado decida: además
tiene que ser la versión activa registrada (HU-46). Sin ella opera como
`experimental`; `/health/detalle` → `modelo_ia.modo_legado_efectivo` lo dice.

## Ronda 2b (se publica junto con la ronda 2)

Sin migraciones; la API solo agrega rutas y campos.

- `POST /api/ia/modelo/versiones/{version}/desactivar` (administrador, cuerpo
  `{"motivo": 10-500 caracteres}`): 200, 404, 409 `version_no_activa`, 422.
  Encadena `MODELO_IA_VERSION_DESACTIVADA`. Después, en `/health/detalle`:
  `modelo_ia.preventivo.motivo_codigo = sin_version_activa_registrada` y
  `modelo_ia.version_activa_registrada = null`. Es la vuelta atrás que restaura
  la línea base (P-12); el botón está en el frontend r2b (Versiones del modelo).
- El 409 de `/activar` y el de registrar con `"activar": true` traen
  `{"detail": "...", "codigos": [...]}`. Códigos nuevos de IA-B05 v6 y v7:
  `horizonte_distinto_del_manifiesto`, `sklearn_distinto_del_manifiesto`,
  `sklearn_incompatible_con_el_instalado`, `manifiesto_distinto_del_historial`.
- IA-D18: cuando el modelo no da clase, una lectura válida en 2-8 °C es normal
  para las alertas, y una alerta ATENDIDA solo absorbe la excursión que continúa
  la anterior; si no, se cierra (`sin_continuidad_tras_atencion`) y se abre una
  PENDIENTE nueva con su aviso.
- SSE: cuando la lectura deja abierto un episodio distinto del que estaba
  abierto (PENDIENTE nueva tras cerrar una ATENDIDA, escalamiento o cambio de
  nivel), el evento es `alerta` (antes `episodio_actualizado`). La ingesta HTTP
  usa el mismo criterio y también avisa cuando el modelo no clasificó.
- Log: el rechazo del artefacto del modelo en modo activo es CRITICAL; sin
  versión activa (tras `/desactivar`), en sombra o experimental es WARNING.

## Comprobaciones que quedan pendientes (físicas u operativas)

- Excursión inducida en producción: alerta, correo, enlace a la alerta,
  reconocimiento, acción correctiva y nueva excursión tras la recuperación
  (HU-21, 23, 27, 52).
- Nodo apagado más de 90 s y recuperado: SIN_DATOS, correo con enlace a
  `/alertas` y cierre con `resuelto_en` (HU-54, INT-11).
- Cuentas de QF, técnico y auditor: recorrer la matriz de roles y desactivar
  un usuario con la sesión abierta (HU-41, 45).
- Distribución de versiones de hash en producción (ver `HASH_CANONICO.md`),
  una verificación de cadena y la exportación del CSV de auditoría (HU-24, 26, 42).
- Servicio cron de respaldo, respaldo nativo de Railway y simulacro de
  restauración (`RESPALDO_RAILWAY.md`, HU-29).
- Modelo preventivo aprobado, registrado y activado (`IA_PREVENTIVA_ACTIVACION.md`,
  HU-16, 18, 20, 34, 46, 47).
