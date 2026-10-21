# 08 — Limitaciones y próximos pasos

## Limitaciones

1. **Eventos independientes insuficientes:** 10 incidentes en Hohenheim (5 en prueba) y 5 episodios
   en el hospital (un equipo, un día). Ninguna conclusión de desempeño es estadísticamente
   concluyente.
2. **Representatividad:** la cámara de Hohenheim opera alrededor de 6–10 °C, con aperturas
   programadas de 5–30 min y gran inercia térmica. No es un refrigerador farmacéutico.
3. **Sin excursiones bajas** (< 2 °C) en ninguna fuente: el comportamiento ante congelación no está
   evaluado.
4. **Causa exógena:** las excursiones de Hohenheim las provocan aperturas de puerta; con temperatura
   sola no se puede anticipar con 30 min de margen una acción humana futura.
5. **Desfase entre sensores** (±1,5 °C entre DHT22): el modelo depende de la temperatura absoluta y
   empeora en un sensor desplazado (SENSOR05).
6. **Puntuación no calibrada:** no debe presentarse como probabilidad.
7. **Supuestos de zona horaria** (CEST en Hohenheim, Lima en el hospital) y semántica inferida de
   las columnas sin nombre del hospital.
8. Movimientos de los sensores 01/02 no registrados en el CSV recibido (excluidos).
9. El MTTD de excursiones actuales corresponde al subsistema determinista y no se midió aquí; su
   cota es el intervalo de muestreo del nodo más la latencia de ingesta.

## Requisitos para TP2 (Experimento D)

1. Desplegar con `IA_PREVENTIVO_MODO=sombra` y `IA_PREVENTIVO_VERSION=rf-preventivo-h30-termico-puerta-1.0.0`
   donde haya MC-38 (si no, la versión `-termico-`).
2. Recolectar como mínimo **14 días por refrigerador** en ≥ 2 refrigeradores reales, con MC-38 y el
   intervalo de muestreo de fábrica (30 s).
3. Generar excursiones **controladas y documentadas** (aperturas prolongadas, desconexión breve)
   hasta reunir **≥ 10 incidentes independientes** con salida de rango observada, incluidas algunas
   bajas si es seguro provocarlas.
4. Evaluar con `evaluar-coldcare`: (a) el modelo de Hohenheim tal cual (transferencia) y (b) un modelo
   reentrenado con datos ColdCare, con partición temporal (no por filas).
5. Criterios de aprobación (los mismos de `CRITERIOS_ACEPTACION`): sensibilidad por incidente ≥ 0,80
   con límite inferior del IC95 ≥ 0,50, ≤ 2 falsas alarmas por sensor-día, mediana de anticipación
   ≥ 5 min y AP superior al mejor baseline. Si se cumplen, registrar `VALIDATED_OFFLINE` y, con la
   firma del responsable, `APPROVED_FOR_DEPLOYMENT`. Solo entonces cabe el modo `activo`, con
   `IA_PREVENTIVO_SHA256` fijado.
6. Evaluar si hace falta calibrar la puntuación (sigmoide sobre una partición separada) antes de
   mostrarla como probabilidad.

## Recomendación

No activar el modelo preventivo para decidir alertas. Mantener la regla determinista para
excursiones críticas y la clasificación existente. Usar el modo sombra para obtener la evidencia
prospectiva que falta. Instalar MC-38 donde sea posible: es la señal que aportó valor predictivo.
