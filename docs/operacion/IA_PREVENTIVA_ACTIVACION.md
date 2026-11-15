# IA preventiva: entrenar, registrar, activar y verificar

Cubre HU-16, HU-18, HU-20, HU-34, HU-46 y HU-47 (P0-3 de la validación final del backlog).

## Cómo se gobierna el modelo (código desde la rama `fix/backlog-r2-ia-respaldo`)

- El registro de versiones de la base (`ai_model_versions`, pantalla "Versiones del modelo")
  tiene una sola versión activa. Esa versión aprueba el artefacto que de verdad clasifica:
  - con `IA_PREVENTIVO_MODO=activo`, el modelo preventivo de
    `src/infrastructure/ai/models/preventivo/<IA_PREVENTIVO_VERSION>/`;
  - en cualquier otro caso, el Random Forest legado (`random_forest_termico.pkl`).
- `POST /api/ia/modelo/versiones/{version}/activar` valida el SHA-256 del artefacto que se va a
  cargar. Con el preventivo activo exige además que la versión sea `IA_PREVENTIVO_VERSION`, que el
  hash sea `IA_PREVENTIVO_SHA256` y que el manifiesto esté en `APPROVED_FOR_DEPLOYMENT`. Si algo
  no cuadra responde 409 con los motivos y deja la fila `ACTIVAR_VERSION_MODELO_IA_RECHAZADO` en la
  bitácora. La versión activa vigente no cambia.
- En modo activo, el modelo preventivo **solo carga** si `IA_PREVENTIVO_VERSION` e
  `IA_PREVENTIVO_SHA256` son los de la versión activa del registro. La comprobación se hace al
  arrancar y después de cada activación, siempre antes de deserializar. Si no coinciden, no se
  carga nada, la ingesta sigue con la regla 2-8 °C y `/health/detalle` muestra el motivo (nunca
  responde 500):

  | `modelo_ia.preventivo.motivo_codigo` | Significado |
  |---|---|
  | `sin_version_activa_registrada` | No hay una versión activa en la base |
  | `version_distinta_de_la_activa` | `IA_PREVENTIVO_VERSION` no es la versión activa |
  | `hash_distinto_del_registrado` | `IA_PREVENTIVO_SHA256` no es el hash registrado |
  | `sha256_no_configurado` / `version_no_configurada` | Falta la variable |
  | `artefacto_no_verificado` | El archivo no coincide con su manifiesto o con el hash |
  | `estado_no_admitido` | El manifiesto no está en `APPROVED_FOR_DEPLOYMENT` |
  | `incompatible` | Otra versión de scikit-learn, otras features u otras clases |
  | `modo_desactivado` | `IA_PREVENTIVO_MODO=desactivado` (lo normal hoy) |

- Cada activación encadena `MODELO_IA_VERSION_ACTIVADA` en `traceability_records`. El evento
  guarda la versión anterior, la nueva, el usuario, la hora, el `modelo` (`preventivo` o `legado`)
  y el `model_hash`. HU-47 esc. 2 cruza cada inferencia con esa historia, que conserva todos los
  periodos aunque una versión se reactive.
- Mientras manda el preventivo, el legado sigue cargado como respaldo experimental. Se verifica
  con su metadata externa, como hoy, y no abre alertas preventivas. Conviene dejar
  `IA_LEGADO_MODO=experimental` (el valor por defecto).

**Comportamiento por defecto (producción hoy).** Con `IA_PREVENTIVO_MODO=desactivado` e
`IA_LEGADO_MODO=experimental` nada cambia: no se carga ningún preventivo, el riesgo efectivo de
una lectura en rango lo decide la regla 2-8 °C y `/health/detalle` muestra
`modelo_ia.en_uso.modelo = "regla_2_8"`.

## Pasos

Las variables `API`, `TOKEN_ADMIN` y `V` se usan en todos los comandos. `API` es la URL pública
del backend, `TOKEN_ADMIN` es el JWT de un ADMINISTRADOR y `V` es la versión.

### 0. Datos (pendiente físico)

Hay que hacer las corridas controladas en hardware (C1…Cn: puerta, carga térmica y corte de
energía) y exportar sus lecturas de `thermal_readings` a CSV. Sin esas corridas no hay
Experimento D y el modelo no puede pasar a `APPROVED_FOR_DEPLOYMENT`.

### 1. Entrenar (offline, entorno `requirements-ml.txt`)

```bash
python -m src.infrastructure.ai.prediccion_preventiva corridas --csv corridas.csv --prueba C3,C4 --horizonte 30 --salida informe_corridas.json
python -m src.infrastructure.ai.prediccion_preventiva entrenar --version 1.1.0
python -m src.infrastructure.ai.prediccion_preventiva listar
```

`entrenar` registra `rf-preventivo-h30-termico-<versión>` (y la variante con puerta) en
`src/infrastructure/ai/models/preventivo/`, como `EXPERIMENTAL` o `VALIDATED_OFFLINE` según sus
criterios. El registro nunca sobrescribe una versión.

### 2. Experimento D y aprobación del artefacto

```bash
python -m src.infrastructure.ai.prediccion_preventiva evaluar-coldcare --csv lecturas_coldcare.csv --version "$V" --salida informe_experimento_d.json
python -m src.infrastructure.ai.prediccion_preventiva estado --version "$V" --nuevo VALIDATED_OFFLINE --motivo "Criterios offline cumplidos" --responsable "<nombre>"
python -m src.infrastructure.ai.prediccion_preventiva estado --version "$V" --nuevo APPROVED_FOR_DEPLOYMENT --motivo "Experimento D aprobado" --responsable "<nombre>" --evidencia informe_experimento_d.json
python -m src.infrastructure.ai.prediccion_preventiva verificar --version "$V"   # imprime el sha256
```

Hay que hacer commit de `models/preventivo/$V/` y `models/preventivo/historial.json` (el artefacto
viaja dentro de la imagen), fusionar en `main` y esperar el despliegue de Railway.

### 3. Registrar la versión en la base (HU-46 esc. 1)

Como ADMINISTRADOR, desde la pantalla "Versiones del modelo" o con:

```bash
curl -sS -X POST "$API/api/ia/modelo/versiones" -H "Authorization: Bearer $TOKEN_ADMIN" -H "Content-Type: application/json" -d '{
  "version": "<V>", "model_hash": "<sha256 del paso 2>",
  "feature_schema_version": "<conjunto_features del manifiesto, p. ej. termico_minimo>",
  "dataset_version": "<corridas y fechas usadas>", "scikit_learn_version": "<manifiesto.versiones.scikit_learn>",
  "python_version": "<manifiesto.versiones.python>", "trained_at": "<manifiesto.entrenado_en>",
  "horizonte_min": 30,
  "metricas": {"f1_macro": 0.0, "incidentes_evaluables": 0, "sensibilidad_incidente": 0.0,
               "ic95_inferior_sensibilidad": 0.0, "falsas_alarmas_por_dia_sensor": 0.0,
               "mediana_anticipacion_min": 0.0, "ap_random_forest": 0.0, "ap_mejor_baseline": 0.0},
  "particiones": {"entrenamiento": "<corridas>", "prueba": "<corridas>", "separacion_min": 30}
}'
```

Las métricas son las del informe del Experimento D y de las corridas. La versión solo se activa si
cumple el criterio principal (`IA_CRITERIO_PRINCIPAL`, por defecto `por_incidente`) y si
`separacion_min ≥ horizonte_min`. Si no cumple, la respuesta trae `activable=false` y la lista
`motivos_bloqueo`. `version` admite como máximo 50 caracteres.

### 4. Configurar el backend en Railway

En el servicio del backend, en este orden:

1. `IA_PREVENTIVO_VERSION=<V>`
2. `IA_PREVENTIVO_SHA256=<sha256>`
3. `IA_PREVENTIVO_MODO=activo`
4. Dejar `IA_LEGADO_MODO=experimental`.
5. Redesplegar.

Hasta el paso 5, `/health/detalle` responde `status=degradado` con
`preventivo.motivo_codigo=sin_version_activa_registrada`. Es el estado esperado y no afecta a la
ingesta ni a las alertas críticas. En producción, `IA_PREVENTIVO_MODO=activo` sin versión ni hash
impide arrancar.

### 5. Activar (HU-46 esc. 2 y 4, HU-16 esc. 4)

```bash
curl -sS -X POST "$API/api/ia/modelo/versiones/$V/activar" -H "Authorization: Bearer $TOKEN_ADMIN"
```

Debe responder 200 con `"activa": true`. Si responde 409, el `detail` dice qué no coincide: la
versión, `IA_PREVENTIVO_SHA256`, el artefacto desplegado, el estado del manifiesto o los criterios.

### 6. Verificar y guardar la evidencia

```bash
curl -sS "$API/health/detalle" -H "Authorization: Bearer $TOKEN_ADMIN" | tee evidencia_health_ia.json
```

Hay que comprobar estos valores en la respuesta:

- `status = "ok"`
- `modelo_ia.disponible = true`: el legado sigue cargado como respaldo
- `modelo_ia.preventivo.disponible = true` y `modelo_ia.preventivo.motivo_codigo = null`
- `modelo_ia.preventivo.es_version_activa_registrada = true`
- `modelo_ia.version_activa_registrada = "<V>"`
- `modelo_ia.en_uso = {"modelo": "preventivo", "version": "<V>", "disponible": true, "aprobado": true}`

Después:

- `GET /api/ia/preventivo` (QF, auditor o admin) debe mostrar `disponible=true`,
  `estado_modelo=APPROVED_FOR_DEPLOYMENT` y `es_version_activa_registrada=true`.
- En la trazabilidad, la cadena `SISTEMA` debe tener un `MODELO_IA_VERSION_ACTIVADA` con
  `modelo=preventivo` y el `model_hash`.
- Con lecturas del nodo dentro de 2 a 8 °C y al menos 15 min de historia:
  `GET /api/ia/inferencia/{lectura_id}` debe devolver `modelo_version=<V>`,
  `version_era_activa_y_aprobada_en_ese_momento=true` y `politica_operativa="aprobada"` (HU-47 esc. 2).
- Una subida controlada debe dar `riesgo_efectivo=riesgo_preventivo` (HU-34 esc. 2) y una alerta
  preventiva con su transición encadenada (HU-20). Esto requiere hardware.

Hay que archivar `evidencia_health_ia.json`, la respuesta de `/activar` y una inferencia aprobada en
`docs/evidencias/`.

### Vuelta atrás

- `IA_PREVENTIVO_MODO=desactivado` y redesplegar: el flujo vuelve a ser el de hoy (regla 2-8 °C y
  legado experimental).
- Otra versión aprobada: actualizar `IA_PREVENTIVO_VERSION`/`IA_PREVENTIVO_SHA256`, redesplegar y
  activarla. La activación anterior queda en la cadena.

## Declaración formal si no se activa antes de la sustentación

Si los pasos 0 a 6 no se completan con datos reales antes de la sustentación, el texto para el
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
> automatizadas: registro, activación validada contra el artefacto que se carga, carga condicionada a
> la versión activa registrada, eventos encadenados y evidencia "aprobada". Las pruebas son
> `tests/integration/test_p03_ia_preventiva_gobernada.py`,
> `tests/unit/test_hu46_preventivo_gobernado_por_registro.py` y
> `tests/unit/test_hu16_p17_verificacion_antes_de_deserializar.py`. Activarlo solo requiere los pasos
> operativos de `docs/operacion/IA_PREVENTIVA_ACTIVACION.md`. Por eso esos escenarios no se cuentan
> como pendientes de software, sino como **no operados por falta de datos de validación**.
