# Alineación del código con el expediente TP1 (Acta 02 incorporada, 28/11/2026)

Fuente: `ColdCare_TP1_Final_Acta02_incorporada_28-11-2026.zip` — Anexo 05 Product Backlog
(54 HU activas, criterios ajustados el 20/11/2026), OE2.I3 (prototipo de alta fidelidad),
OE3.I1 y la revisión integral del 28/11/2026. Se auditó cada escenario Gherkin contra
Backend, Frontend e IoT; esta tabla resume lo que cambió en el código.

## Brechas corregidas

| HU / escenario | Cambio |
|---|---|
| HU-15 esc. 3 | El límite de 768 B del contrato v1.0 también rige para el formato anterior: se acusa `rejected` cuando la identidad es recuperable (`main.py`). |
| HU-18 esc. 1 y 4 | Cada inferencia preventiva guarda `inferido_en` (hora real de la inferencia) y `conjunto_features` (versión del esquema de características) — migración `0044`. `GET /api/ia/preventivo/predicciones` devuelve además `modelo_sha256` y la probabilidad de ambas clases. |
| HU-20 esc. 4 | La transición de una alerta preventiva registra el estado ANTERIOR (antes registraba el nuevo al reabrir): `None` al crear, el estado de atención al actualizar, `episodio_cerrado` al reabrir. |
| HU-22 esc. 4 / HU-11 esc. 3 | El conflicto de payload con la misma identidad también compara la duración de la apertura de puerta. `captured_at` sigue fuera (el firmware lo reserializa al reintentar). |
| HU-31 esc. 4 | `GET /api/lecturas` admite `limite` hasta 10 000: 24 h a 10 s son 8 640 lecturas por equipo. |
| HU-34 esc. 2 | En `IA_PREVENTIVO_MODO=sombra` la clase del modelo viaja como `ia_sombra` en la lectura (API y SSE) y el tablero la rotula «IA en modo sombra»; el riesgo efectivo no cambia y no se abre alerta. |
| HU-38 esc. 4 | El PDF declara en portada y cierre: «Este documento constituye evidencia del monitoreo y de la trazabilidad de los registros; no es una declaración de cumplimiento sanitario integral ni de conformidad DIGEMID.» |
| HU-44 esc. 1 | La confirmación en la consola del broker mide y encadena `ventana_s` (segundos entre la operación y su confirmación). |
| HU-49 esc. 1 | `PATCH /api/dispositivos/{id}/sensores` (solo administrador): cambia los sensores habilitados (DS18B20 obligatorio) con valor anterior, nuevo, actor y hora en el historial de configuración. |
| HU-14 esc. 3 | Evidencia de observabilidad de intentos no autorizados sin canal en texto plano (`docs/evidencias/HU-14_evidencia.md`). |

## Configuración de producción (Railway)

`IA_PREVENTIVO_MODO=sombra`, `IA_PREVENTIVO_VERSION=rf-preventivo-h30-termico-puerta-1.0.0`,
`IA_PREVENTIVO_SHA256` = SHA-256 del `modelo.joblib` de esa versión
(`docs/operacion/HU-55_despliegue_railway.md`). `IA_LEGADO_MODO=experimental`.

## Frontend (prototipo OE2.I3)

Menú persistente en tres grupos con los nombres del prototipo (Operación: Monitoreo, Alertas,
Historial, Guía de uso · Registros y revisión: Integridad de registros, Reportes, Clasificador de
riesgo · Administración: Auditoría, Usuarios, Dispositivos). Monitoreo con la conexión del equipo
junto a su nombre, la fila de estadísticas (mínima, máxima, promedio, lecturas fuera de 2–8 °C)
bajo la curva y la tarjeta del equipo junto a las lecturas recientes. Historial con filtro de
conexión (RF-12). Curva cortada a los 30 s sin datos (3 × 10 s, HU-54).

## Pendiente que no es código

- Verificaciones en hardware y en el entorno desplegado que el Anexo 05 deja en la columna
  «Trabajo pendiente para cumplir DoD» (MAE/sesgo, LittleFS, broker real, SMTP real).
- HU-09 esc. 2 (certificado vencido) depende de compilar mbedTLS con hora en el firmware: decisión
  de alcance pendiente del equipo.
- HU-44 esc. 2 con un broker administrado por API (EMQX 5 completo): la rotación cambia el token en
  el broker en la misma petición. En producción (EMQX Cloud Serverless, `BROKER_USUARIOS_MODO=manual`)
  el orden exigido —token primero al nodo como relevo y después a la consola— se cumple.
