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
| Inyector con tabla de resultados | `python infra-local/scripts/inyector_casos.py --casos … --csv …` | TEM-05, RIE-01, RIE-03, LAT-02, MQT-03, MQT-06 (colisión), MQT-08 (device_id ajeno) |
| Latencia (p50/p95/p99/máx) | `python -m scripts.informe_latencia --desde … --device-id …` | LAT-02, RIE-03 |
| Cadenas de 10 000 (v1, v2, v3) y su alteración | `python -m scripts.cadenas_prueba generar\|alterar` | INT-02, INT-03, INT-04, INT-06, INT-08 |
| Huecos y duplicados de `seq_no` | `python -m scripts.huecos_seq --device-id …` | CON-02, ENE-02 |

## LAT-02: una excursión repetida no envía otro correo

Con la ventana de normalización (`ALERTA_VENTANA_NORMALIZACION_MINUTOS`, 15 min) una excursión que se repite
**reabre la misma alerta** y no genera otro correo (`notificada_en` ya está puesto). Usa
`ALERTA_VENTANA_NORMALIZACION_MINUTOS=0` y **alterna una lectura fuera de rango (12 °C) y una en rango (5 °C)**
(`inyector_casos.py --casos latencia`); cada subida abre un episodio nuevo. Declara esa configuración en el paper.
Bajar la ventana a 1 minuto no basta si las excursiones se repiten en menos de un minuto. No hace falta un
`device_id` por corrida (cada uno necesitaría su credencial y ACL).

La latencia de `NOTIFICACION_ENVIADA` se cuenta desde que el backend empieza a procesar la lectura: la espera en
cola del despachador no aparece. Separa las excursiones (`--pausa`) en vez de mandarlas en ráfaga.

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
- **Límites de las herramientas.** `huecos_seq` no detecta lecturas perdidas al inicio ni al final de un arranque
  (solo huecos entre las que llegaron): para decir «0 pérdidas» compáralo con lo que emitió el nodo. Una alerta
  sin SMTP o sin destinatario se queda en «Pendiente» en la interfaz.
- **`colision_clave`** no tiene regla de cierre automático todavía: el incidente queda abierto hasta resolverlo.
- **Banco de pruebas en contenedor:** `infra-local/docker-compose.pruebas.yml` pasa estas opciones al backend y usa
  una base aparte (`coldcare_prueba`).
- **Levantar el banco:** `docker compose -p coldcare-develop -f docker-compose.yml -f docker-compose.pruebas.yml --profile backend up -d --build`
  (siempre con `-p coldcare-develop`: sin él se crea otro stack con Postgres vacío y conflicto de puertos). La base
  `coldcare_prueba` nace sin usuarios ni dispositivos: siembra con `scripts.seed_dev` y `crear_dispositivo.py`.
