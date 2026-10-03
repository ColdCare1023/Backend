# Evidencia — HU-11: Publicación QoS 1 con idempotencia — lado backend

Entorno: `infra-local/` (PostgreSQL 16, EMQX 5.8.6 con TLS 8883 y CA propia, backend FastAPI en Docker), nodo ESP32 DevKitC real con firmware ColdCare1023/IoT `develop` (v1.2.0) salvo donde se indique, frontend ColdCare1023/Frontend `main`. Fechas: 2–3 de octubre de 2026.

> Reemplaza la evidencia anterior de esta HU, que se apoyaba en pruebas sobre módulos simulados (cliente MQTT sin conexión real, broker y navegador falsos en Python) y no ejercitaba el sistema real.
## Defectos corregidos
1. La identidad de la lectura pasa a ser `(device_id, boot_id, seq_no)`. La restricción única por
   `(device_id, timestamp)` hacía chocar lecturas **distintas** de dos arranques sin NTP: el INSERT fallaba,
   no había acuse y el nodo reintentaba para siempre (bloqueo de su cola FIFO, observado en vivo).
   Migración 0015.
2. **Registro de conflicto de payload** (no existía): misma identidad con contenido distinto → se conserva la
   original y la recibida queda auditada como `LECTURA_CONFLICTO`.
3. El backend se suscribía en QoS 0 (ver HU-12): el tramo broker→backend no era QoS 1.

## Resultados en vivo
| Caso | Resultado |
|---|---|
| Misma lectura publicada 3 veces | 3 acuses, **1 fila** en PostgreSQL |
| Misma identidad, temperatura 4.0 → 9.5 | Se conserva 4.0; 9.5 auditada; acuse `duplicate` (v1) |
| Dos arranques con el mismo timestamp | Se guardan ambas |

Pruebas: `test_mismo_timestamp_con_otra_identidad_se_guardan_ambas`,
`test_misma_identidad_con_valores_distintos_es_conflicto`, `test_reenvio_identico_no_es_conflicto`,
`test_reenvio_es_duplicate_y_contenido_distinto_es_conflicto`.
El lado firmware (espera de PUBACK, reintento con la misma identidad, paso a buffer) está en
ColdCare1023/IoT `docs/evidencias/HU-11_evidencia.md`.
