# Herramientas para las pruebas de escritorio

Todas las opciones de falla **se niegan a arrancar con `ENVIRONMENT=production`** (la configuración lanza
`ValueError`): `IA_PRUEBA_RETARDO_S`, `IA_PRUEBA_EXCEPCION`, `MQTT_PRUEBA_FALLO_ANTES_COMMIT` y
`SMTP_SEGURIDAD=ninguna`.

| Necesidad | Herramienta | Casos |
|---|---|---|
| IA lenta o que falla | `IA_INFERENCIA_TIMEOUT_S` (1 s por defecto), `IA_PRUEBA_RETARDO_S`, `IA_PRUEBA_EXCEPCION=true` | RIE-01, RIE-03, LAT-02, TEM-05 |
| Falla justo antes del COMMIT | `MQTT_PRUEBA_FALLO_ANTES_COMMIT=excepcion\|salir` | MQT-01 |
| Bandeja de correo local | `infra-local/docker-compose.correo-prueba.yml` (Mailpit, SMTP 1025, web 8025) | LAT-02, ALE-01 |
| SMTP que falla | `python -m scripts.smtp_falla --modo rechazar\|timeout\|temporal\|permanente\|ok` | ALE-02 |
| Inyector con tabla de resultados | `python infra-local/scripts/inyector_casos.py --casos … --csv …` | TEM-05, RIE-01, RIE-03, LAT-02, MQT-03, MQT-06, MQT-08 |
| Latencia (p50/p95/p99/máx) | `python -m scripts.informe_latencia --desde … --device-id …` | LAT-02, RIE-03 |
| Cadenas de 10 000 (v1, v2, v3) y su alteración | `python -m scripts.cadenas_prueba generar\|alterar` | INT-02, INT-03, INT-04, INT-06, INT-08 |
| Huecos y duplicados de `seq_no` | `python -m scripts.huecos_seq --device-id …` | CON-02, ENE-02 |

## LAT-02: una excursión repetida no envía otro correo

Con la ventana de normalización (`ALERTA_VENTANA_NORMALIZACION_MINUTOS`, 15 min) una excursión que se repite
**reabre la misma alerta** y no genera otro correo (`notificada_en` ya está puesto). Para que las 100 corridas
sintéticas midan algo, usa **un `device_id` distinto por corrida** o baja la ventana a 1 minuto. De lo contrario
solo se mide el primer correo.

## Criterios de aceptación que cambian

- **MQT-08 (`device_id` distinto del tópico):** se audita `LECTURA_RECHAZADA_SUPLANTACION` y **no hay acuse**: esa
  identidad no es confiable. Un payload de más de 5 KB deja `LECTURA_RECHAZADA_TAMANO` (una fila por minuto).
- **SEG-01 (credencial revocada):** el acuse `rejected` sale antes de expulsar la sesión; la expulsión es una vez
  por ventana y la auditoría tiene tope (`suprimidos_desde_anterior`).
- **ALE-01:** quitar «duración» del criterio. **ALE-02:** pedir la cadena de trazabilidad del dispositivo, no
  `audit_logs`.
- **Inferencia:** si la IA no responde en `IA_INFERENCIA_TIMEOUT_S`, la lectura queda con
  `estado_inferencia=fallida` (`timeout_inferencia` o `excepcion_inferencia`) y la regla 2-8 °C sigue decidiendo.
- **Modelo alterado:** al arrancar, un hash que no coincide deja `MODELO_RECHAZADO` en la bitácora.
