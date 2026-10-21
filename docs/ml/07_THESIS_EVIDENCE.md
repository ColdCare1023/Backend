# 07 — Evidencia para la tesis

## Aporte de Random Forest en ColdCare

El sistema separa dos decisiones que antes se mezclaban:

1. **Excursión crítica**: regla determinista (temperatura válida fuera de 2–8 °C). Es inmediata,
   no depende de la IA y genera la alerta crítica aunque el modelo no cargue (probado en
   `test_alerta_critica_no_depende_de_que_el_modelo_cargue`).
2. **Riesgo preventivo**: clasificador Random Forest binario sobre una ventana causal de 15 min,
   que estima si habrá una excursión en los próximos 30 min. Solo se evalúa sobre instantes en rango.

## Qué es evidencia utilizable en TP1

| Evidencia | Tipo |
|---|---|
| Auditoría reproducible de dos datasets públicos (DOI, licencia, hashes, exclusiones motivadas) | **Resultado** |
| Definición formal de la etiqueta preventiva, sin fuga (H = 30 min) | Método |
| Pipeline causal con paridad entrenamiento/producción comprobada por pruebas | **Resultado** |
| Partición temporal con purga/embargo y auditoría automática de fugas (0 fugas) | **Resultado** |
| Comparación con 4 baselines con métricas por observación y por evento | **Resultado** |
| A1 (solo temperatura): no supera a la regla de tendencia (AP 0,18 frente a 0,23) | **Resultado negativo** |
| A2 (temperatura + puerta): supera a todos los baselines (AP 0,51 frente a 0,23; mediana de anticipación 16,5 min) | **Resultado**, con solo 5 incidentes en prueba |
| Transferencia cámara → refrigerador hospitalario: fracasa (AP ≈ prevalencia) | **Resultado negativo** |
| El modelo legado sintético no constituye evidencia preventiva | **Resultado de auditoría** |
| Integración segura en FastAPI (modo sombra, registro versionado, rollback) | **Resultado** |

**Afirmación defendible ante el jurado:** "Se implementó y verificó un pipeline de Random Forest
preventivo libre de fugas. Con datos públicos, la señal térmica sola no anticipa excursiones mejor
que una regla de tendencia; añadir el estado de la puerta sí mejora la anticipación de forma
sustancial. La evidencia disponible (5 incidentes independientes en prueba) es insuficiente para
aprobar un modelo, y la transferencia entre recintos falla. Por eso el sistema queda en modo sombra,
para validarlo con datos propios del ESP32."

**No se debe afirmar:** que el modelo alcanza un F1 ≥ 0,85 (eso era el modelo sintético), que
existe validación clínica o farmacéutica, ni que los resultados de Hohenheim u hospital equivalen a
una botica de Lima.

## Pendiente para TP2

Experimento D con datos ColdCare (ver 08). Hipótesis a contrastar: **[Hipótesis]** un modelo
`termico_puerta` entrenado con datos del propio refrigerador supera a la regla de tendencia con ≤ 2
falsas alarmas por día.

## Referencias

- Henrichs, E., Stoll, F., Krupitzer, C. (2025). *Temperature and Humidity Time Series of Cold
  Storage Room Monitoring* [dataset]. Zenodo. https://doi.org/10.5281/zenodo.15130001
- Henrichs, E., Stoll, F., Krupitzer, C. (2025). Towards Self-Adaptive Monitoring of Storage
  Environments with Distributed Sensor Systems. *IEEE ACSOS-C*, 85–90.
  https://doi.org/10.1109/ACSOS-C66519.2025.00033
- Lon Kan Perez, I. B. K. (2026). *Multi-Node IoT Dataset for Real-Time Hospital Cold-Chain
  Monitoring* [dataset]. Zenodo. https://doi.org/10.5281/zenodo.22101912
- Breiman, L. (2001). Random Forests. *Machine Learning*, 45, 5–32.
  https://doi.org/10.1023/A:1010933404324
- Pedregosa, F. et al. (2011). Scikit-learn: Machine Learning in Python. *JMLR*, 12, 2825–2830.
- scikit-learn: *Common pitfalls*, *Cross-validation*, *RandomForestClassifier*, *Probability
  calibration*, *Model persistence* (https://scikit-learn.org/stable/).
- Saito, T., Rehmsmeier, M. (2015). The Precision-Recall Plot Is More Informative than the ROC Plot
  When Evaluating Binary Classifiers on Imbalanced Datasets. *PLoS ONE*, 10(3), e0118432.
  https://doi.org/10.1371/journal.pone.0118432
- Clopper, C. J., Pearson, E. S. (1934). The use of confidence or fiducial limits illustrated in the
  case of the binomial. *Biometrika*, 26(4), 404–413. https://doi.org/10.1093/biomet/26.4.404
