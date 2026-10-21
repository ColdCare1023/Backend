"""Predicción preventiva de excursiones térmicas con Random Forest.

Pipeline offline (ingesta → calidad → eventos → etiquetas → features →
partición → entrenamiento → evaluación → registro) y servicio de inferencia
que reutiliza el MISMO extractor de features (ver `features.py`). Ver
`docs/ml/` para la metodología y los resultados.
"""
