# 06 — Integración con el backend FastAPI

## 1. Arquitectura (DDD, sin reemplazar lo existente)

| Capa | Archivo | Rol |
|---|---|---|
| Dominio | `domain/entities/prediccion_preventiva.py` | Entidad *predicción* (no medición) |
| Dominio | `domain/repositories/i_prediccion_preventiva_repository.py` | Puerto de persistencia |
| Aplicación | `application/use_cases/predecir_riesgo_preventivo.py` | Historial, predicción, aplicación según modo, registro |
| Aplicación | `application/use_cases/registrar_lectura_termica.py` | **Modificado:** parámetro opcional `prediccion_preventiva` (None = flujo idéntico al anterior) |
| Infraestructura | `infrastructure/ai/prediccion_preventiva/servicio.py` | Carga verificada del modelo e inferencia con precedencia |
| Infraestructura | `infrastructure/database/models.py` + `repositories/prediccion_preventiva_repository.py` | Tabla `predicciones_preventivas` |
| Infraestructura | `alembic/versions/0025_predicciones_preventivas.py` | Tabla + triggers de solo anexado (PostgreSQL y SQLite) |
| Interfaz | `interface/main.py` (consumidor MQTT) | Cableado del caso de uso |
| Interfaz | `interface/api/ia_router.py` | `GET /api/ia/preventivo`, `GET /api/ia/preventivo/predicciones`; `/api/ia/modelo` declara `datos_simulados` |
| Configuración | `infrastructure/config.py` | `IA_PREVENTIVO_MODO`, `IA_PREVENTIVO_VERSION`, `IA_PREVENTIVO_SHA256` |

El entrenamiento es **offline** (CLI). No hay rutas HTTP para entrenar, subir ni activar modelos.

## 2. Modos

| `IA_PREVENTIVO_MODO` | Efecto |
|---|---|
| `desactivado` (por defecto) | Flujo exactamente igual al anterior. No se consulta ni se carga nada. |
| `sombra` | Se predice y se guarda en `predicciones_preventivas`; `nivel_riesgo`, alertas, SSE y cadena hash **no cambian**. Admite modelos EXPERIMENTAL / VALIDATED_OFFLINE / APPROVED. Es el modo recomendado para TP2 (evidencia prospectiva). |
| `activo` | Además decide `nivel_riesgo` (normal / riesgo_preventivo). **Solo** carga un modelo `APPROVED_FOR_DEPLOYMENT`; en producción exige versión y SHA-256 (si faltan, el arranque falla). |

## 3. Precedencia por lectura

1. Temperatura interna ausente o no finita → `SENSOR_FAULT`.
2. Excursión actual (2–8 °C, regla determinista) → `EXCURSION_EN_CURSO`: el modelo no se ejecuta;
   `riesgo_efectivo = excursion_critica` y la alerta salen del flujo existente.
3. Modelo no cargado, alterado o incompatible → `MODELO_NO_DISPONIBLE` (en modo activo se conserva
   la clasificación legada: la ingesta nunca se detiene).
4. Reloj no sincronizado o ventana de 15 min con menos de 80 % de cobertura → `HISTORIAL_INSUFICIENTE`.
5. Features (el mismo extractor del entrenamiento) → puntuación → clase según el umbral del manifiesto.
   Cualquier excepción → `FALLO_INFERENCIA`.

`SIN_DATOS` (ausencia de telemetría) lo sigue calculando el estado operativo del dispositivo
(`dispositivos_router`): sin lectura no hay inferencia. Lecturas manuales y simuladas (dispositivos
demo) **no se predicen**. El historial se lee con `listar_recientes_por_device(limite=120)` (cubre
15 min incluso con el intervalo mínimo de 10 s del ESP32).

## 4. Contratos preservados

- Respuestas de lecturas, SSE y alertas: sin cambios de forma. En modo activo los campos existentes
  (`nivel_riesgo`, `origen_clasificacion`, `estado_inferencia`, `motivo_no_inferencia`,
  `modelo_version`, `vector_features_ia`) se rellenan con los valores ya admitidos por el frontend.
  `confianza_ia` y `probabilidades_ia` quedan en `null` porque la puntuación **no está calibrada**:
  no se presenta como probabilidad.
- Cadena SHA-256: el payload de `LECTURA_TERMICA` mantiene sus mismas claves. Las predicciones viven
  en su propia tabla de solo anexado (con trigger), con el SHA-256 del artefacto y la versión del
  modelo en cada fila.
- `/api/ia/modelo`: solo se **añaden** `datos_simulados: true` y `estado_evaluacion`, que el frontend
  ya tipaba (`useModeloIA.ts`) y con los que ya mostraba "no verificado".

## 5. Seguridad y despliegue (Railway)

- Artefactos en `src/infrastructure/ai/models/preventivo/<versión>/` **dentro de la imagen**, igual
  que el modelo legado: el disco de Railway es efímero y la imagen es inmutable y versionada. No se
  necesita un volumen ni un servicio nuevo. Los datasets **no** entran en la imagen (`.dockerignore`)
  ni en Git (`.gitignore`).
- La carga con joblib/pickle solo ocurre tras verificar el SHA-256 del archivo contra su manifiesto
  y, si se configuró, contra `IA_PREVENTIVO_SHA256`. Los nombres de versión se validan con una
  expresión regular (sin rutas). [Decisión] No se adoptó `skops`: añadiría una dependencia de
  producción cuya compatibilidad con scikit-learn 1.9 no está verificada. La mitigación es aceptar
  solo artefactos propios con hash verificado.
- Rollback: cambiar `IA_PREVENTIVO_VERSION` y `IA_PREVENTIVO_SHA256` a una versión anterior (el
  registro nunca sobrescribe). Poner `IA_PREVENTIVO_MODO=desactivado` vuelve al flujo legado.
- Rendimiento: una inferencia = remuestrear ≤ 120 lecturas + `predict_proba` de una fila con
  `n_jobs=1`. El modelo se carga una sola vez por proceso.
