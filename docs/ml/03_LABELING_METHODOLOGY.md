# 03 — Metodología de etiquetado

Código: `etiquetas.py`, `eventos.py`.

## 1. Excursiones, episodios e incidentes

- **Excursión (punto):** T̃(g) válida y fuera de [2, 8] °C. Es la misma regla que
  `LecturaTermica.es_excursion_confirmada`.
- **Episodio:** tramos fuera de rango de una serie separados por menos de 10 min se funden en uno
  (una oscilación alrededor de 8 °C es un solo evento). Inicio s = primer punto fuera de rango. Si el
  primer dato del segmento ya está fuera de rango, la salida **no se observó** (`inicio_observado =
  false`, p. ej. un sensor instalado caliente) y ese episodio no cuenta como evento anticipable.
- **Incidente:** episodios de un mismo `entorno_id` cuyos intervalos (± 10 min) se solapan. Es la
  **unidad independiente** para contar eventos, particionar y estimar incertidumbre.

## 2. Variable objetivo (H = 30 min)

Para un punto t de un segmento S:

```
estado(t) =
  fuera_de_rango        si T̃(t) ∉ [2,8]          (excursión actual → regla determinista)
  en_episodio           si t ∈ [s_k, f_k] de algún episodio y T̃(t) ∈ [2,8]
  positivo  (y=1)       si ∃ s_k : t < s_k ≤ t + H
  negativo  (y=0)       si no, t + H ≤ fin(S) y cobertura((t, t+H]) ≥ 0,9
  no_etiquetable        en otro caso (horizonte incompleto o cobertura insuficiente)
```

`excursion_critica` **no** es una clase del modelo: un instante fuera de rango nunca entra al
clasificador. La etiqueta usa información posterior a t solo porque se construye sobre datos
históricos; las features nunca leen más allá de t (verificado por pruebas). Además se guarda
`minutos_a_excursion` = s_k − t para medir la anticipación.

## 3. Por qué no se cuentan ventanas como eventos

Antes de cada episodio hay hasta 30 instantes positivos por sensor, y varios sensores registran el
mismo incidente. Se informa siempre el número de **incidentes**, no de filas, y las métricas por
evento se agregan por episodio e incidente.

## 4. Horizonte

[Decisión] H = 30 min es el horizonte **principal, fijado de antemano**, como parámetro
experimental. H = 15 min se evalúa solo como análisis de sensibilidad (Experimento A3) y **no se
eligió el horizonte con mejores métricas**. [Limitación] En Hohenheim las excursiones las provocan
aperturas de puerta programadas, que ocurren unos 7 min antes de superar 8 °C: con temperatura sola,
ningún modelo puede anticipar con 30 min de margen un evento causado por una acción humana futura.
El horizonte define *qué se etiqueta*, no *cuánto se logra anticipar*: esto último se mide aparte
(anticipación por episodio). La validez clínica del horizonte debe contrastarse con los requisitos
de la tesis y el tiempo de reacción del personal de botica.
