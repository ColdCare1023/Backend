# IA preventiva: de las corridas a la activación, verificación y vuelta atrás

Cubre HU-16, HU-18, HU-20, HU-34, HU-46 y HU-47. Es el procedimiento operativo de la sección 6 del
plan de pruebas de la IA preventiva (`PLAN_PRUEBAS_IA.md`, «Procedimientos de activación y de
reversión»); los casos IA-xxx citados son los de ese plan.

## Cómo se gobierna el modelo

- El registro de versiones de la base (`ai_model_versions`, pantalla "Versiones del modelo")
  tiene **como máximo** una versión activa. Esa versión aprueba el artefacto que de verdad
  clasifica:
  - con `IA_PREVENTIVO_MODO=activo`, el modelo preventivo de
    `src/infrastructure/ai/models/preventivo/<IA_PREVENTIVO_VERSION>/`;
  - en cualquier otro caso, el Random Forest legado (`random_forest_termico.pkl`).
- `POST /api/ia/modelo/versiones/{version}/activar` (solo ADMINISTRADOR) valida el artefacto que se
  va a cargar. Si algo no cuadra responde 409, deja la fila `ACTIVAR_VERSION_MODELO_IA_RECHAZADO` en
  la bitácora y la versión activa vigente no cambia. El cuerpo del 409 trae `detail` (los motivos en
  texto, como siempre) y `codigos`, un código estable por motivo y en el mismo orden:

  | Código | Motivo |
  |---|---|
  | `version_preventiva_no_configurada` | Falta `IA_PREVENTIVO_VERSION` |
  | `version_distinta_de_la_desplegada` | La versión no es la de `IA_PREVENTIVO_VERSION` |
  | `sha256_no_configurado` | Falta `IA_PREVENTIVO_SHA256` |
  | `sha256_distinto_del_registrado` | `IA_PREVENTIVO_SHA256` no es el `model_hash` registrado |
  | `artefacto_distinto_del_registrado` | El archivo desplegado no tiene el `model_hash` registrado (también legado) |
  | `manifiesto_ilegible` | No se pudo leer el manifiesto |
  | `manifiesto_distinto_del_historial` | El SHA-256 de `manifiesto.json` no es el último anotado en `historial.json`: se editó fuera de la CLI (IA-B05 v7) |
  | `manifiesto_no_aprobado` | El manifiesto no está en `APPROVED_FOR_DEPLOYMENT` |
  | `horizonte_distinto_del_manifiesto` | `horizonte_min` registrado ≠ `configuracion.horizonte_min` del manifiesto (IA-B05 v6) |
  | `sklearn_distinto_del_manifiesto` | `scikit_learn_version` registrado ≠ `versiones.scikit_learn` del manifiesto (IA-B05 v6) |
  | `sklearn_incompatible_con_el_instalado` | El manifiesto se entrenó con otra versión menor de scikit-learn que la instalada; no cargaría (IA-B05 v6) |
  | `falta_horizonte`, `falta_esquema_features`, `faltan_particiones`, `separacion_menor_que_horizonte` | Falta evidencia de evaluación (HU-46) |
  | `criterio_principal_desconocido`, `faltan_metricas_criterio_principal`, `no_cumple_criterio_principal` | Criterio principal (`IA_CRITERIO_PRINCIPAL`) |

- `POST /api/ia/modelo/versiones/{version}/desactivar` (solo ADMINISTRADOR), con
  `{"motivo": "<10 a 500 caracteres>"}`, deja el registro **sin versión activa**. Responde 200 con la
  versión ya inactiva, 404 si no está registrada, 409 `version_no_activa` si no es la activa (o si
  otra petición simultánea ya la desactivó) y 422 si el motivo no es válido. Encadena
  `MODELO_IA_VERSION_DESACTIVADA` (quién, cuándo, versión, motivo y `model_hash`, enlazado al hash
  anterior de la cadena `SISTEMA`) y deja `DESACTIVAR_VERSION_MODELO_IA` en la bitácora. Es la vuelta
  atrás que restaura la línea base (ver más abajo). Tras ella `/health/detalle` muestra
  `modelo_ia.preventivo.motivo_codigo = sin_version_activa_registrada` (ese valor exacto) y
  `modelo_ia.version_activa_registrada = null`.
- `/activar` y `/desactivar` comparten un candado de base de datos que se toma antes de leer la
  versión activa y la hora del cambio: si llegan a la vez se aplican uno detrás del otro. Así la
  base, la cadena de eventos y la consulta de HU-47 esc. 2 dicen lo mismo. Ejemplo: activar Y y
  desactivar X a la vez termina en 200/200 si se desactivó primero, o en 200/409 si Y ya había
  reemplazado a X.
- En modo activo, el modelo preventivo **solo carga** si `IA_PREVENTIVO_VERSION` e
  `IA_PREVENTIVO_SHA256` son los de la versión activa del registro. La comprobación se hace al
  arrancar, después de cada activación y después de cada desactivación, siempre antes de
  deserializar. Si no coinciden, no se carga nada, la ingesta sigue con la regla 2-8 °C y
  `/health/detalle` muestra el motivo (nunca responde 500):

  | `modelo_ia.preventivo.motivo_codigo` | Significado | Log |
  |---|---|---|
  | `sin_version_activa_registrada` | No hay una versión activa en la base (antes de activar o tras `/desactivar`) | WARNING |
  | `version_distinta_de_la_activa` | `IA_PREVENTIVO_VERSION` no es la versión activa | CRITICAL |
  | `hash_distinto_del_registrado` | `IA_PREVENTIVO_SHA256` no es el hash registrado | CRITICAL |
  | `sha256_no_configurado` / `version_no_configurada` | Falta la variable | WARNING |
  | `artefacto_no_verificado` | El archivo no coincide con su manifiesto o con el hash | CRITICAL |
  | `estado_no_admitido` | El manifiesto no está en `APPROVED_FOR_DEPLOYMENT` | CRITICAL |
  | `incompatible` | Otra versión de scikit-learn, otras features u otras clases | CRITICAL |
  | `modo_desactivado` | `IA_PREVENTIVO_MODO=desactivado` (lo normal hoy) | WARNING (una vez por proceso) |

  Cada motivo sale una sola vez por proceso, en la primera consulta del modelo (`/health/detalle`
  o la primera inferencia), y otra vez después de cada `/activar` o `/desactivar`, que obligan a
  reverificar. Con `modo_desactivado` ese WARNING es esperado y no requiere acción.

  HU-46 esc. 3: un artefacto rechazado se registra como error CRITICAL en el log y, al arrancar,
  como `MODELO_RECHAZADO` encadenado en la bitácora.
- Cada activación encadena `MODELO_IA_VERSION_ACTIVADA` (versión anterior, nueva, usuario, hora,
  `modelo` y `model_hash`) y cada desactivación `MODELO_IA_VERSION_DESACTIVADA`. HU-47 esc. 2 cruza
  cada inferencia con esa historia: una lectura clasificada después de una desactivación no se
  atribuye a ninguna versión.
- Mientras manda el preventivo, el legado sigue cargado como respaldo experimental: se verifica con
  su metadata externa y no abre alertas preventivas. Se deja `IA_LEGADO_MODO=experimental` (el valor
  por defecto); nunca `activo`.

**Comportamiento por defecto (producción hoy).** Con `IA_PREVENTIVO_MODO=desactivado`,
`IA_LEGADO_MODO=experimental` y ninguna versión activa no se carga ningún preventivo, el riesgo
efectivo de una lectura en rango lo decide la regla 2-8 °C (el legado deja su clase como evidencia
`ia_experimental`) y `/health/detalle` muestra `modelo_ia.en_uso.modelo = "regla_2_8"` y
`status = "ok"`. A esto se le llama **línea base** en este documento.

## Flujo

Exportar → etiquetar y entrenar → registrar el artefacto → sombra → aprobar → registrar la versión
→ activar → verificar. Las variables `API`, `TOKEN_ADMIN` y `V` se usan en todos los comandos:
`API` es la URL pública del backend, `TOKEN_ADMIN` el JWT de un ADMINISTRADOR y `V` la versión.
Los prerrequisitos P-x son los de la sección 2.1 del plan.

### 1. Datos y exportación (corridas de banco)

Las corridas se hacen con el nodo de banco `ESP32_BANCO_01` contra el stack local, nunca con el
refrigerador ni la identidad de la farmacia, y solo con el protocolo PE-INC-01 aprobado (P-11;
sección 3.2 del plan). Cada evento tiene su hoja de corrida (`corrida_id`, inicio y fin UTC, tipo,
responsable). `simular_excursion.py` y `demo excursion` no sirven: marcan `simulated=true` y el
backend no las predice.

Se exportan las lecturas del nodo de banco a CSV con el exportador de corridas (P-1), con estas
columnas: `device_id`, `timestamp` (o `timestamp_estimado` si falta), `temperatura_interna`,
`apertura_refrigerador`, `time_quality`, `ds18b20_status` o `sensor_fault` y `corrida_id`. Solo
lecturas del dispositivo (no manuales ni simuladas). `importar_coldcare_csv` descarta y cuenta las
lecturas `unsynced` y las de `SENSOR_FAULT`.

### 2. Etiquetar, entrenar y evaluar con las corridas (offline, `requirements-ml.txt`)

```bash
python -m src.infrastructure.ai.prediccion_preventiva corridas --csv corridas.csv --prueba C3,C4 --horizonte 30 --salida informe_corridas.json
```

`corridas` etiqueta (y = 1 si empieza una excursión en (t, t + H]), excluye ventanas incompletas,
divide por corrida con separación ≥ H, compara el Random Forest con los baselines y reporta los dos
juegos de criterios, con y sin el SHT31. **No registra ningún modelo**: es un informe.

`entrenar` **no** forma parte de este flujo. Entrena con los datasets externos (Hohenheim y
hospital) y registra `rf-preventivo-h30-termico-<versión>`; solo sirve para reproducir los
experimentos offline A–C (`docs/ml/09_REPRODUCIBILITY.md`). No se encadena con `corridas`.

### 3. Registrar el artefacto entrenado con las corridas

Hace falta P-2: una ruta que registre en `models/preventivo/<V>/` el modelo entrenado con las
corridas, con su manifiesto (`configuracion.horizonte_min`, `versiones.scikit_learn`, features,
umbral) y su entrada en `historial.json`. Mientras P-2 no esté cerrado no hay ruta soportada. Un
manifiesto escrito o editado a mano no se puede activar: `/activar` responde 409
`manifiesto_distinto_del_historial`.

El artefacto entra como `EXPERIMENTAL` y pasa a `VALIDATED_OFFLINE` si cumple los criterios offline:

```bash
python -m src.infrastructure.ai.prediccion_preventiva estado --version "$V" --nuevo VALIDATED_OFFLINE --motivo "Criterios offline cumplidos (informe_corridas.json)" --responsable "<nombre>"
python -m src.infrastructure.ai.prediccion_preventiva verificar --version "$V"   # imprime el sha256
```

Se hace commit de `models/preventivo/$V/` y de `models/preventivo/historial.json` (el artefacto viaja
dentro de la imagen), se fusiona en `main` y se espera el despliegue. El registro nunca sobrescribe
una versión. El modelo debe entrenarse con la versión de scikit-learn de `requirements.txt` o
demostrar paridad (P-6).

### 4. Sombra (fase S, obligatoria antes de activo)

En Railway, en un solo lote: `IA_PREVENTIVO_MODO=sombra`, `IA_PREVENTIVO_VERSION=<V>`,
`IA_PREVENTIVO_SHA256=<sha>`. Redesplegar y medir la duración del redespliegue.

- `/health/detalle`: `preventivo.disponible=true` y `en_uso.modelo=regla_2_8` (sombra no decide).
- Las predicciones se acumulan en `GET /api/ia/preventivo/predicciones` y no se abren alertas
  preventivas.
- Mantener al menos 72 h para medir falsas alarmas en el refrigerador real.

### 5. Experimento D y aprobación del artefacto

```bash
python -m src.infrastructure.ai.prediccion_preventiva evaluar-coldcare --csv lecturas_coldcare.csv --version "$V" --salida informe_experimento_d.json
python -m src.infrastructure.ai.prediccion_preventiva estado --version "$V" --nuevo APPROVED_FOR_DEPLOYMENT --motivo "Experimento D aprobado" --responsable "<nombre>" --evidencia informe_experimento_d.json
```

`estado` anota en `historial.json` el SHA-256 del manifiesto nuevo. Commit de los dos archivos,
fusión en `main` y despliegue. Para la fase A hace falta además el acta GO firmada (sección 2.3).

### 6. Registrar la versión en la base (HU-46 esc. 1)

Como ADMINISTRADOR, desde la pantalla "Versiones del modelo" o con:

```bash
curl -sS -X POST "$API/api/ia/modelo/versiones" -H "Authorization: Bearer $TOKEN_ADMIN" -H "Content-Type: application/json" -d '{
  "version": "<V, nombre exacto del directorio, ≤50>", "model_hash": "<artefacto.sha256 del manifiesto>",
  "feature_schema_version": "<manifiesto.conjunto_features>",
  "dataset_version": "<convención P-4, ≤50>", "scikit_learn_version": "<manifiesto.versiones.scikit_learn, exacta>",
  "python_version": "<manifiesto.versiones.python>", "trained_at": "<manifiesto.entrenado_en>",
  "horizonte_min": <manifiesto.configuracion.horizonte_min>,
  "metricas": {"f1_macro": 0.0, "incidentes_evaluables": 0, "sensibilidad_incidente": 0.0,
               "ic95_inferior_sensibilidad": 0.0, "falsas_alarmas_por_dia_sensor": 0.0,
               "mediana_anticipacion_min": 0.0, "ap_random_forest": 0.0, "ap_mejor_baseline": 0.0},
  "particiones": {"entrenamiento": "<corridas>", "prueba": "<corridas>", "separacion_min": 30}
}'
```

Los valores salen del manifiesto, no del entorno de ejecución: `/activar` rechaza un `horizonte_min`
o un `scikit_learn_version` distintos de los del manifiesto. Las métricas son las de
`informe_corridas.json` y del Experimento D, revisadas por una segunda persona. Si la versión no
cumple el criterio principal o `separacion_min < horizonte_min`, la respuesta trae
`activable=false` y `motivos_bloqueo`.

### 7. Configurar el backend en Railway

Avisar al QF. En un solo lote:

- `IA_PREVENTIVO_MODO=activo`, `IA_PREVENTIVO_VERSION=<V>`, `IA_PREVENTIVO_SHA256=<sha>`
- `IA_LEGADO_MODO=experimental` (nunca `activo`)
- `IA_CRITERIO_PRINCIPAL=por_incidente`, `IA_INFERENCIA_TIMEOUT_S=1.0`,
  `ALERTA_PREVENTIVA_MINUTOS_NORMALES=10`

Redesplegar. Hasta el paso 8, `/health/detalle` responde `status=degradado` con
`preventivo.motivo_codigo=sin_version_activa_registrada` y decide la regla 2-8 °C. Debe durar
10 min como máximo. En producción, `IA_PREVENTIVO_MODO=activo` sin versión ni hash impide arrancar.

### 8. Activar (HU-46 esc. 2 y 4, HU-16 esc. 4)

```bash
curl -sS -X POST "$API/api/ia/modelo/versiones/$V/activar" -H "Authorization: Bearer $TOKEN_ADMIN"
```

Debe responder 200 con `"activa": true`; carga en el mismo proceso, sin reiniciar. Si responde 409,
`codigos` dice qué no cuadra (tabla de arriba). Cualquier falla en los pasos 8 y 9 dispara la vuelta
atrás.

### 9. Verificar y guardar la evidencia

```bash
curl -sS "$API/health/detalle" -H "Authorization: Bearer $TOKEN_ADMIN" | tee evidencia_health_ia.json
```

- `status = "ok"`
- `modelo_ia.disponible = true` (el legado sigue cargado como respaldo)
- `modelo_ia.preventivo.disponible = true`, `motivo_codigo = null` y
  `es_version_activa_registrada = true`
- `modelo_ia.version_activa_registrada = "<V>"`
- `modelo_ia.en_uso = {"modelo": "preventivo", "version": "<V>", "disponible": true, "aprobado": true}`
- `arranque.workers = 1` y un solo consumidor MQTT en los logs (HU-12 E4)

Después:

- `GET /api/ia/preventivo` (QF, auditor o admin): `modo=activo`, `disponible=true`,
  `estado_modelo=APPROVED_FOR_DEPLOYMENT`, `sha256=<sha>` y `es_version_activa_registrada=true`.
- Trazabilidad: un `MODELO_IA_VERSION_ACTIVADA` en la cadena `SISTEMA` con `modelo=preventivo` y el
  `model_hash`; auditoría `ACTIVAR_VERSION_MODELO_IA` (IA-B07).
- Con lecturas del nodo dentro de 2 a 8 °C y al menos 16 min de historia:
  `GET /api/ia/inferencia/{lectura_id}` devuelve `modelo_version=<V>`,
  `version_era_activa_y_aprobada_en_ese_momento=true` y `politica_operativa="aprobada"`.
- Una subida controlada en el banco da `riesgo_efectivo=riesgo_preventivo` y una alerta preventiva
  con su transición encadenada (HU-20).

Archivar `evidencia_health_ia.json`, la respuesta de `/activar` y una inferencia aprobada en
`docs/evidencias/`. Avisar al QF y empezar el monitoreo de 24 a 72 h (IA-G01).

## Vuelta atrás

Disparadores: los de la sección 6.3 del plan (violación de GS-1, cadena no íntegra, modelo en uso no
disponible más de 15 min, 5xx, MQTT caído, más de 4 falsas alarmas en 24 h, memoria, o lo pide el QF).

### R-A. Desactivar la versión (restaura la línea base)

1. Avisar al QF y desactivar la versión activa:

   ```bash
   curl -sS -X POST "$API/api/ia/modelo/versiones/$V/desactivar" -H "Authorization: Bearer $TOKEN_ADMIN" \
     -H "Content-Type: application/json" -d '{"motivo": "<disparador y referencia del incidente>"}'
   ```

   Responde 200 con `"activa": false`. Surte efecto en el mismo proceso, sin redesplegar: el
   preventivo deja de decidir de inmediato. Queda `MODELO_IA_VERSION_DESACTIVADA` encadenado y
   `DESACTIVAR_VERSION_MODELO_IA` en la bitácora, así que el rollback sí queda trazado.
2. En Railway, en un solo lote, `IA_PREVENTIVO_MODO=desactivado` (o `sombra` si el problema son
   solo las falsas alarmas y se quiere seguir midiendo). `IA_LEGADO_MODO` se deja en
   `experimental`: sin versión activa, nada fija un hash al legado y este no queda rechazado.
   `IA_PREVENTIVO_VERSION` e `IA_PREVENTIVO_SHA256` pueden quedar como están. Redesplegar.
3. Verificar:
   - Tras el paso 1: `modelo_ia.version_activa_registrada = null`,
     `preventivo.motivo_codigo = sin_version_activa_registrada` y `status = degradado` (mientras
     el entorno siga en `activo`; no afecta a la ingesta ni a las alertas críticas).
   - Tras el paso 2 (línea base): `en_uso.modelo = regla_2_8`,
     `preventivo.motivo_codigo = modo_desactivado`, `modelo_ia.motivo_codigo = null` (el legado
     carga) y `status = ok`.
   - Las lecturas en rango quedan con `riesgo_efectivo = normal`, igual que antes de cualquier
     activación (con el legado experimental, su clase queda marcada `ia_experimental`). Una
     excursión sigue generando alerta crítica PENDIENTE y correo. Los episodios preventivos
     abiertos se cierran tras 10 min en normal.
   - HU-12 E4 como en el paso 9.
4. Registrar el cambio en la planilla y en la carpeta de evidencias, con la respuesta de
   `/desactivar` y el evento encadenado.

Si la API no responde, se aplica solo el paso 2 con `IA_LEGADO_MODO=desactivado` (el legado quedaría
rechazado mientras V siga activa en la base) y se ejecuta el paso 1 en cuanto la API vuelva: hasta
entonces el sistema no está en la línea base.

**Reactivar después de R-A.** No queda versión activa, así que no carga nada en el arranque: hay
que repetir los pasos 7 a 9 (entorno en `activo` y `POST /activar` de V, que vuelve a validar el
artefacto y abre un periodo nuevo en la historia).

### R-B. Volver a una versión anterior W (solo si W está APPROVED y registrada)

1. Poner la versión y el SHA de W en el entorno y redesplegar. Estado intermedio:
   `version_distinta_de_la_activa`.
2. `POST /api/ia/modelo/versiones/<W>/activar`.
3. Verificar que el evento tiene `version_anterior=V`.

### R-C. Restaurar la base no aplica a la IA

Un rollback del modelo nunca restaura la base: activar y desactivar solo escriben filas de solo
anexado (`ai_model_versions`, `audit_logs`, `traceability_records`). Restaurar producción borraría
esa evidencia (HU-46 E4) y las lecturas posteriores al respaldo. El respaldo queda para
recuperación ante desastre (HU-29).

## Declaración formal si no se activa antes de la sustentación

Si los pasos 1 a 9 no se completan con datos reales antes de la sustentación, el texto para el
backlog y la tesis es este:

> La clasificación preventiva con Random Forest y la alerta preventiva que deriva de ella quedan
> **fuera de operación en el despliegue de producción** evaluado: HU-18 esc. 1 y 4, HU-20 esc. 1 a 4,
> HU-34 esc. 2, HU-46 esc. 1, 3 y 4 aplicados al modelo preventivo y HU-47 esc. 2. El motivo es que no
> se dispone de corridas controladas en hardware ni del Experimento D con datos ColdCare reales que
> permitan aprobar un modelo con evidencia válida (el modelo legado se entrenó con escenarios
> sintéticos y se ejecuta en modo experimental, sin decidir el riesgo). En producción, el riesgo
> efectivo de toda lectura válida lo determina la regla determinista de 2 a 8 °C (HU-21, HU-34 esc. 1
> y 3) y el monitoreo, la persistencia, la trazabilidad y las alertas críticas operan sin depender de
> la IA (HU-16 esc. 3). El mecanismo de gobierno sí está implementado y verificado con pruebas
> automatizadas: registro, activación validada contra el artefacto y el manifiesto que se cargan,
> carga condicionada a la versión activa registrada, desactivación que restaura la línea base,
> eventos encadenados y evidencia "aprobada". Las pruebas son
> `tests/integration/test_p03_ia_preventiva_gobernada.py`,
> `tests/integration/test_p12_desactivar_version_modelo.py`,
> `tests/integration/test_p12_desactivar_concurrencia_postgres.py`,
> `tests/integration/test_p12_activar_desactivar_concurrencia_postgres.py`,
> `tests/integration/test_hu46_e3_rechazo_critico_al_arrancar.py`,
> `tests/unit/test_b05_hu46_e3_gobierno_preventivo.py`,
> `tests/unit/test_hu46_preventivo_gobernado_por_registro.py` y
> `tests/unit/test_hu16_p17_verificacion_antes_de_deserializar.py`. Activarlo solo requiere los pasos
> operativos de este documento. Por eso esos escenarios no se cuentan como pendientes de software,
> sino como **no operados por falta de datos de validación**.
