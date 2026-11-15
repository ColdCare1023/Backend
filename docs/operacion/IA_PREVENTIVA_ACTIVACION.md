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
  | `esquema_features_distinto_del_manifiesto` | `feature_schema_version` registrado ≠ `conjunto_features` del manifiesto (IA-B05 v6; es el esquema que muestra la evidencia de HU-47) |
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
  reemplazado a X. Registrar con `"activar": true` toma ese candado antes de registrar (antes de
  escribir en la cadena `SISTEMA`), en el mismo orden que `/activar` y `/desactivar`; así no hay
  interbloqueo con un `/desactivar` simultáneo.
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

  HU-46 esc. 3: un artefacto rechazado del modelo que decide (modo activo) se registra como error
  CRITICAL en el log y, al arrancar, como `MODELO_RECHAZADO` encadenado en la bitácora. En
  `IA_PREVENTIVO_MODO=sombra` el modelo no decide nada y el mismo rechazo es un WARNING (también
  queda `MODELO_RECHAZADO`). El legado es distinto: todo rechazo de su artefacto
  (`checksum_invalido`, `hash_distinto_de_la_version_activa`, `sin_verificacion_de_integridad`,
  `incompatible`, `error_de_lectura`) es CRITICAL en cualquier modo en que se carga, también con
  `IA_LEGADO_MODO=experimental`, porque su clase sigue quedando como evidencia de cada lectura. El
  servicio lo registra al cargarlo y el arranque, al auditarlo, con el mismo nivel. Con
  `IA_LEGADO_MODO=desactivado` no se carga ni se audita.
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
`apertura_refrigerador`, `time_quality`, `ds18b20_status` o `sensor_fault` y `corrida_id`. El
exportador agrega `temperatura_ambiental` y `humedad_ambiental` (SHT31), que solo usan la ablación y
`termico_ambiente`: sin ellas, o con corridas sin SHT31, `termico_ambiente` queda "no estimable" y
`termico_minimo` se evalúa y se registra igual. Solo
lecturas del dispositivo (no manuales ni simuladas). `importar_coldcare_csv` descarta y cuenta las
lecturas `unsynced` y las de `SENSOR_FAULT`. En las hojas de corrida, `uso=prueba` marca las corridas
de prueba y `uso=validacion` las que eligen el umbral (P-3); el resumen del exportador trae las dos
listas (`prueba` y `validacion`).

### 2. Etiquetar, entrenar y evaluar con las corridas (offline, `requirements-ml.txt`)

```bash
python -m src.infrastructure.ai.prediccion_preventiva corridas --csv corridas.csv --prueba C4,C5 --validacion C3 --horizonte 30 --salida informe_corridas.json
```

`corridas` etiqueta (y = 1 si empieza una excursión en (t, t + H]), excluye ventanas incompletas,
divide por corrida con separación ≥ H, elige el umbral fuera de la muestra de entrenamiento (P-3),
compara el Random Forest con los baselines y reporta los dos juegos de criterios, con y sin el SHT31.
Sin `--registrar` **no registra ningún modelo**: es un informe. Si una corrida de prueba o de
validación no tiene temperatura y humedad ambiental válidas, `termico_ambiente` queda "no estimable"
con ese motivo y `ablacion_sht31` en `null`; una corrida que no está en el CSV termina con código 2.

**Umbral de decisión (P-3).** El umbral es el de F1 máximo sobre puntuaciones de filas que el modelo
que las puntúa no vio al ajustarse, y las filas se separan por corrida, nunca por fila:

- Con `--validacion` (partición de validación): esas corridas no ajustan el modelo. Un modelo ajustado
  con las demás corridas de entrenamiento las puntúa, y ese mismo modelo es el que se evalúa en
  prueba y se registra. Una corrida de ajuste a menos de H de una de validación (mismo equipo) se
  descarta, igual que frente a la prueba. Una corrida de validación no puede ser de prueba.
- Sin `--validacion` (respaldo cuando hay pocas corridas): se deja una corrida fuera. Cada corrida de
  entrenamiento la puntúa un modelo ajustado sin ella (ni las que quedan a menos de H), el umbral sale
  de esas puntuaciones y el modelo final se ajusta después con todas las corridas de entrenamiento.
  Úselo cuando, al separar la validación, el ajuste se quedaría con menos de 2 corridas con excursión
  o la validación sin ninguna.
- Si no quedan puntuaciones fuera de muestra con ambas clases (por ejemplo, una sola corrida de
  entrenamiento con excursión) o hay menos de 2 corridas de ajuste, el conjunto queda **no estimable**.
  Ya no se usa 0,5 por defecto.
- El informe (`resultados.<conjunto>.seleccion_umbral`) y el manifiesto (`particiones`,
  `seleccion_umbral` y `origen_umbral`) dicen qué corridas ajustaron el modelo, cuáles eligieron el
  umbral (`validacion`, con `metodo_umbral`) y cuáles lo evaluaron (`prueba`). La regla de pendiente
  elige su umbral con las mismas filas.

`entrenar` **no** forma parte de este flujo. Entrena con los datasets externos (Hohenheim y
hospital) y registra `rf-preventivo-h30-termico-<versión>`; solo sirve para reproducir los
experimentos offline A–C (`docs/ml/09_REPRODUCIBILITY.md`). No se encadena con `corridas`.

### 3. Registrar el artefacto entrenado con las corridas (P-2)

En el entorno de `requirements-ml.txt` (scikit-learn exactamente el de `requirements.txt`), desde la
raíz del backend:

```bash
export V=rf-corridas-h30-termico-1.0.0   # minúsculas, dígitos, '.' y '-'; 50 caracteres como máximo
python -m src.infrastructure.ai.prediccion_preventiva corridas --csv corridas.csv --prueba C4,C5 --validacion C3 --horizonte 30 \
    --registrar --version "$V" --conjunto termico_minimo --salida informe_corridas.json
```

Entrena igual que el paso 2 (solo el conjunto elegido tiene que poder evaluarse; la ablación con el
SHT31 puede quedar "no estimable") y registra su modelo con `registro.registrar`, la misma ruta que
anota los hashes en el historial:

- `src/infrastructure/ai/models/preventivo/$V/modelo.joblib`;
- `src/infrastructure/ai/models/preventivo/$V/manifiesto.json`, en estado `EXPERIMENTAL`, con lo que
  comprueban la carga y `/activar`: `configuracion` (incluido `horizonte_min`), `conjunto_features`,
  `features`, `umbral_decision`, `versiones` (`scikit_learn`, `python`, `numpy`, `pandas`...) y
  `artefacto.sha256`. Lleva además `requisitos.scikit_learn_fijado`, la procedencia de los datos
  (`datos`: nombre y SHA-256 del CSV exportado, `dataset_version`, rango de fechas y cada corrida con
  su rol, su rango y sus lecturas), las particiones de P-3, las métricas de prueba y
  `criterios_aceptacion`;
- una entrada `registrado` en `models/preventivo/historial.json` con el SHA-256 del artefacto y el del
  manifiesto.

Antes de escribir comprueba que el servicio de inferencia sabe servir el conjunto
(`paridad_servicio`): carga el modelo en sombra desde un registro temporal y le pasa, por
`ServicioPrediccionPreventiva.predecir`, las lecturas del CSV de una corrida de prueba hasta su lectura
más reciente en rango con ventana completa. El servicio debe clasificarla (estado `ok`) con el mismo
vector y la misma puntuación que el extractor del entrenamiento (diferencia ≤ 5·10⁻⁷, el redondeo a 6
decimales con que el servicio guarda la evidencia). Si no, no registra nada.

Después relee lo escrito: el SHA-256 del artefacto coincide con el manifiesto, el del manifiesto está
anotado en `historial.json` y la versión carga en modo sombra. Imprime un JSON con `sha256`,
`verificacion`, `paridad_servicio`, `cuerpo_registro_api` (el cuerpo del paso 6 ya armado con los
valores del manifiesto) y `advertencias`. `--salida` guarda el informe completo y el registro. Para
ensayar sin tocar el repositorio se agrega `--directorio-registro <directorio temporal>`, que también
aceptan `listar`, `verificar` y `estado`.

`dataset_version` es por defecto `corridas-<12 primeros hex del SHA-256 del CSV>`, una propuesta para
P-4 que se cambia con `--dataset-version`.

`--conjunto termico_ambiente` (SHT31) depende de que el servicio reciba el SHT31 (HU-17, P0-4).
Mientras `predecir` no le pase la temperatura y la humedad ambiental al extractor, la comprobación de
paridad lo rechaza (`el conjunto termico_ambiente no se puede servir: … HISTORIAL_INSUFICIENTE …`),
porque el modelo nunca clasificaría. Con HU-17 integrado se registra, y en producción una lectura cuya
ventana de 15 min no tenga temperatura y humedad ambiental válidas queda sin clasificación con el
motivo `ventana_15min_sin_contexto_ambiental`. `termico_minimo` no depende del SHT31.

**Advertencia, sin negativa.** Si ninguna corrida de ajuste o de validación es del mismo equipo que
las de prueba, no hay separación que medir: `cuerpo_registro_api` va sin `particiones.separacion_min`
y POST `/activar` responderá 409 `faltan_particiones`. La CLI registra igual (la versión sirve en
sombra) y lo avisa en `advertencias` y con `Advertencia: …` en la salida de error. Para poder activar,
se registra una versión nueva que ajuste con al menos una corrida del equipo de prueba separada al
menos H; la separación no se escribe a mano.

Se niega con código de salida 2 y `No se registra el modelo: …`, sin escribir nada, en estos casos:

| Caso | Texto del motivo |
|---|---|
| El scikit-learn instalado no es el de `requirements.txt` (P-6) | `el scikit-learn instalado (…) no es el fijado en requirements.txt (…)` |
| `requirements.txt` no fija scikit-learn con `==` | `requirements.txt no fija scikit-learn con '=='` |
| La versión ya existe | `la versión … ya existe en el registro; el registro nunca sobrescribe una versión` |
| La versión figura en `historial.json` aunque se borró su directorio | `la versión … ya existe en el registro: figura en historial.json …` |
| `historial.json` no se puede leer | `No se pudo leer historial.json: no se puede comprobar que la versión … sea nueva` |
| Versión con mayúsculas u otros caracteres, o de más de 50 | `Nombre de versión inválido` / `… 50 como máximo` |
| Menos de 2 corridas de ajuste | `hacen falta al menos 2 corridas de ajuste` |
| Las corridas de ajuste no tienen ambas clases | `entrenamiento sin ambas clases` |
| No hay umbral fuera de muestra | `sin umbral fuera de muestra` |
| La prueba no tiene ambas clases | `las corridas de prueba … no tienen ambas clases` |
| Una corrida de prueba o de validación inexistente o inválida | `las corridas no se pueden usar: …` |
| Una corrida de prueba o de validación sin observaciones válidas para el conjunto que se registra (p. ej. sin SHT31 con `termico_ambiente`) | `las corridas no se pueden usar: las corridas […] no tienen observaciones válidas para el conjunto …` |
| El servicio de inferencia no clasifica con el vector del entrenamiento | `el conjunto … no se puede servir: …` |

Un manifiesto escrito o editado a mano sigue sin poder activarse: `/activar` responde 409
`manifiesto_distinto_del_historial`.

El artefacto entra como `EXPERIMENTAL`. La CLI no decide los criterios: pasa a `VALIDATED_OFFLINE` solo
si `criterios_aceptacion` del manifiesto los cumple, revisado con la regla de cuatro ojos:

```bash
python -m src.infrastructure.ai.prediccion_preventiva estado --version "$V" --nuevo VALIDATED_OFFLINE --motivo "Criterios offline cumplidos (informe_corridas.json)" --responsable "<nombre>"
python -m src.infrastructure.ai.prediccion_preventiva verificar --version "$V"   # imprime el sha256
```

Se hace commit de `models/preventivo/$V/` y de `models/preventivo/historial.json` (el artefacto viaja
dentro de la imagen), se fusiona en `main` y se espera el despliegue. El registro nunca sobrescribe
una versión. P-6 lo impone la propia CLI: no registra si el scikit-learn instalado no es el de
`requirements.txt`.

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

`corridas --registrar` imprime este cuerpo en `cuerpo_registro_api` (sin las métricas no estimables,
que la base no admite nulas). Los valores salen del manifiesto, no del entorno de ejecución: `/activar` rechaza un `horizonte_min`,
un `scikit_learn_version` o un `feature_schema_version` distintos de los del manifiesto. Las métricas son las de
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
