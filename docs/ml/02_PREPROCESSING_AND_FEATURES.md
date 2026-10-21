# 02 — Preprocesamiento y features

Código: `src/infrastructure/ai/prediccion_preventiva/` (`ingesta.py`, `calidad.py`, `features.py`).
Parámetros: `configuracion.py` (un único lugar, compartido por entrenamiento e inferencia).

## 1. Esquema canónico

`fuente, entorno_id, serie_id, timestamp (UTC), temperatura_interna, puerta_abierta`. `entorno_id`
identifica el recinto físico: sensores del mismo recinto **no** son independientes.

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

**Calidad (no se fabrica nada):** se exige que T̃(t) y T̃(t−1) sean válidos, una cobertura ≥ 80 % en
cada ventana y un valor válido en el inicio de cada ventana. Si no se cumple, el estado es
`HISTORIAL_INSUFICIENTE`: se informa el estado y no se predice.

**Excluidas a propósito:** humedad (interior en Hohenheim, exterior en ColdCare: magnitudes
distintas), hora del día (memorizaría el horario del protocolo), identificadores de sensor, archivo
o dataset, y la temperatura auxiliar del hospital (sin documentar). Una prueba automática verifica
que ninguna feature con esos nombres entre al modelo.

## 5. Paridad entrenamiento ↔ producción

Ambos caminos terminan en `calcular_matriz(ventanas)`. En entrenamiento se usan las ventanas
deslizantes de la rejilla; en producción, `features_en_instante()` remuestrea el historial crudo
anclando la rejilla en el instante de la lectura. La prueba
`test_paridad_entre_entrenamiento_e_inferencia` compara ambos caminos punto a punto (muestreo de 5,
30 y 60 s, con fluctuación del reloj y huecos), con tolerancia 1e-9.
