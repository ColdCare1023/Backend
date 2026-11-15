# 02 — Preprocesamiento y features

Código: `src/infrastructure/ai/prediccion_preventiva/` (`ingesta.py`, `calidad.py`, `features.py`).
Parámetros: `configuracion.py` (un único lugar, compartido por entrenamiento e inferencia).

## 1. Esquema canónico

`fuente, entorno_id, serie_id, timestamp (UTC), temperatura_interna, puerta_abierta`. `entorno_id`
identifica el recinto físico: sensores del mismo recinto **no** son independientes. Las corridas de
ColdCare (`scripts/exportar_corridas.py`) traen además `temperatura_ambiental` y `humedad_ambiental`
(SHT31), que la rejilla guarda como `temp_ambiental` y `humedad`.

## 2. Limpieza

1. Orden cronológico por serie. 2. Duplicados exactos eliminados. 3. Timestamps repetidos con
valores distintos: se anulan **ambos** (no hay forma de saber cuál es el verdadero). 4. Valores fuera
del rango físico del sensor (−55…80 °C) → NaN. 5. Cada descarte se cuenta con su motivo.
**Nunca** se interpola ni se imputa con valores futuros.

## 3. Rejilla causal

Toda fuente (5 s, 60 s, 10–3600 s en el ESP32) se lleva a una rejilla de paso Δ = 60 s:

  T̃(g) = media{ T(tᵢ) : g − A < tᵢ ≤ g },  A = 90 s

Si no hay lecturas, el punto queda en NaN. [Decisión] Se usa A > Δ porque, con muestreo de 60 s y
±1 s de fluctuación del reloj, una agregación de exactamente 60 s dejaba el 37 % de los puntos
vacíos (dos lecturas en un punto, cero en el siguiente; hallazgo durante la auditoría). Con A = 90 s
el hospital pasa de ~2 000 puntos vacíos a 29.

**Segmentación:** más de 3 puntos seguidos sin dato (> 180 s) cortan la serie. Ninguna ventana ni
etiqueta cruza un corte; los huecos cortos quedan NaN y los gobiernan las reglas de cobertura.

## 4. Features (conjunto `termico_minimo`, 28 variables)

Ventana W = 15 min (16 puntos, incluido t). Para w ∈ {5, 10, 15} min:

| Feature | Definición |
|---|---|
| `temp_actual` | T̃(t) |
| `dist_limite_sup`, `dist_limite_inf` | 8 − T̃(t), T̃(t) − 2 |
| `delta_1min` | T̃(t) − T̃(t−1) |
| `media_w`, `min_w`, `max_w`, `std_w` | estadísticos de T̃ en [t−w, t], sin NaN |
| `pendiente_w` | pendiente por mínimos cuadrados (°C/min) en [t−w, t] |
| `delta_w` | T̃(t) − primer valor válido en el inicio de la ventana |
| `max_subida_15m`, `max_bajada_15m`, `frac_subidas_15m` | sobre las diferencias por minuto |
| `aceleracion` | pendiente_5m − pendiente_15m |
| `frac_cerca_limite_15m` | fracción de la ventana a ≤ 1 °C de un límite |
| `frac_fuera_rango_15m` | fracción de la ventana fuera de 2–8 °C (recuperación reciente) |

Variante `termico_puerta` (+2): `puerta_actual` y `frac_puerta_15m`. Solo es utilizable con MC-38
instalado. En Hohenheim, la puerta proviene del **protocolo**, no de un sensor.

Variante `termico_ambiente` (+4, HU-17 y ablación con y sin SHT31 de `corridas.py`):
`temp_amb_actual` y `humedad_actual` (SHT31 en t), `dif_interna_ambiente` (T̃(t) − temperatura
ambiental en t) y `media_temp_amb_15m`. Se entrena solo con corridas de ColdCare, donde la humedad es
la del SHT31 en todas las series.

**Calidad (no se fabrica nada):** se exige que T̃(t) y T̃(t−1) sean válidos, una cobertura ≥ 80 % en
cada ventana y un valor válido en el inicio de cada ventana. `termico_puerta` exige además la puerta
en t, y `termico_ambiente` la temperatura ambiental y la humedad en t (el punto t es la media de las
lecturas de los últimos 90 s, así que una sola lectura sin SHT31 se cubre con la anterior). Si no se
cumple, el estado es `HISTORIAL_INSUFICIENTE`: se informa el estado y no se predice. El motivo
(`motivo` de la predicción; `motivo_no_inferencia` de la lectura en modo activo) es:

| Motivo | Cuándo |
|---|---|
| `reloj_no_sincronizado` | La lectura actual llega con `time_quality=unsynced` |
| `ventana_15min_sin_cobertura_suficiente` | La temperatura interna no cumple las reglas de cobertura (también si falta la puerta en `termico_puerta`) |
| `ventana_15min_sin_contexto_ambiental` | La temperatura interna alcanza, pero el conjunto es `termico_ambiente` y falta la temperatura ambiental o la humedad del SHT31 en t |

**Excluidas a propósito:** humedad en `termico_minimo` y `termico_puerta` (interior en Hohenheim,
exterior en ColdCare: magnitudes distintas), hora del día (memorizaría el horario del protocolo),
identificadores de sensor, archivo o dataset, y la temperatura auxiliar del hospital (sin
documentar). Una prueba automática verifica que ninguna feature con esos nombres entre al modelo.

## 5. Paridad entrenamiento ↔ producción

Ambos caminos terminan en `calcular_matriz(ventanas)`. En entrenamiento se usan las ventanas
deslizantes de la rejilla, segmento por segmento. En producción, `evaluar_en_instante()` (y su
envoltorio `features_en_instante()`) hace lo mismo con el historial crudo:

- la misma rejilla y la misma media causal (`calidad.remuestrear`), anclada en el instante de la
  lectura, con la puerta y el SHT31 como canales extra;
- las mismas filas: telemetría del nodo con hora sincronizada. Una lectura con el DS18B20 en falla no
  aporta temperatura, pero su puerta y su SHT31 cuentan, como en la exportación de corridas;
- la misma segmentación (`calidad.segmentar`), aplicada a una rejilla que empieza `hueco_max_s` antes
  de la ventana. Si la ventana toca un corte (más de 180 s sin temperatura interna), sus puntos
  anteriores no cuentan, igual que el relleno NaN del entrenamiento. Sin esto, la puerta o el SHT31
  de un DS18B20 en falla de 6 min entraba en `frac_puerta_15m` y `media_temp_amb_15m` del servicio
  y no en las del entrenamiento.

`ServicioPrediccionPreventiva.predecir` construye cada señal solo si el conjunto del manifiesto la
usa (`features.CANALES_POR_CONJUNTO`), con NaN donde la lectura no la trae.

Pruebas:

- `tests/unit/test_preventivo_features.py::test_paridad_entre_entrenamiento_e_inferencia` compara
  los dos caminos en cada punto de la rejilla, para `termico_minimo`, `termico_puerta` y
  `termico_ambiente`, con muestreo de 5, 30 y 60 s, fluctuación del reloj, huecos corto y largo,
  DS18B20 en falla (dentro del segmento y cortando la serie) y SHT31 sin dato. Tolerancia 1e-9.
- `tests/unit/test_hu17_contexto_sht31_servicio.py::test_paridad_del_servicio_con_el_entrenamiento`
  hace lo mismo de punta a punta: `predecir` con el historial de `LecturaTermica` que le pasa la
  ingesta contra `calidad.preparar_rejillas` + `features_de_rejillas`.

[Corrección, ronda 3] Hasta la ronda 2 esta sección presentaba la paridad como general (también
para `termico_puerta`), pero la prueba solo cubría `termico_minimo`. Además, el servicio no recibía
el SHT31: un modelo `termico_ambiente` cargaba y nunca clasificaba, con el motivo
`ventana_15min_sin_cobertura_suficiente`.

**Conjuntos servibles.** El servicio solo carga un manifiesto cuyo conjunto está en
`CANALES_POR_CONJUNTO`, con señales que `predecir` sabe construir (`servicio.CANALES_SERVIDOS`) y
con la lista de features del extractor. Si no, no carga (`motivo_codigo = incompatible`) y
`/activar` responde 409 `esquema_features_no_servible`. Un conjunto nuevo del entrenamiento no se
sirve hasta declararlo ahí y cubrirlo con la prueba de paridad.

## 6. Modelo legado (Random Forest de 10 variables): historial causal (HU-17)

El clasificador legado (`ClasificarRiesgoTermicoUseCase._construir_features`) también usa una sola
función en entrenamiento (`train_model_v3`) y en servicio:

- Solo entran lecturas capturadas **antes** de t. El repositorio corta en `timestamp < t`
  (`listar_recientes_por_device(..., hasta=t)`), así que una lectura reenviada desde el buffer
  LittleFS no ve las que llegaron antes que ella pero se capturaron después.
- El historial se lleva a una rejilla de 1 min hacia atrás desde t (la cadencia con que se genera el
  entrenamiento), tomando en cada punto la lectura válida más cercana, dentro de una ventana de
  35 min (el episodio de entrenamiento más largo). La pendiente se calcula en °C/min por tiempo real;
  antes se calculaba por índice y a 10 s salía 6 veces menor que en entrenamiento.
- Con menos de 2 puntos de rejilla la lectura queda `historial_insuficiente_para_tendencia`.

Las pruebas `tests/unit/test_hu17_features_causales.py` comprueban que las lecturas futuras no cambian
el vector, que la misma serie a 10 s, 30 s y 60 s da el mismo vector y que el camino de entrenamiento y el
de servicio coinciden tick a tick. El artefacto 4.0.0-binario no se reentrenó: con un tick por minuto sus
features solo cambian en la pendiente de los episodios con mensajes perdidos.

