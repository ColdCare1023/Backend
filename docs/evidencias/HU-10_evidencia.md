# Evidencia — HU-10: Autenticación de dispositivo

Entorno: `infra-local/` (PostgreSQL 16, EMQX 5.8.6 con TLS 8883 y CA propia, backend FastAPI en Docker), nodo ESP32 DevKitC real con firmware ColdCare1023/IoT `develop` (v1.2.0) salvo donde se indique, frontend ColdCare1023/Frontend `main`. Fechas: 2–3 de octubre de 2026.

> Reemplaza la evidencia anterior de esta HU, que se apoyaba en pruebas sobre módulos simulados (cliente MQTT sin conexión real, broker y navegador falsos en Python) y no ejercitaba el sistema real.
## Configuración
EMQX con autenticación usuario/clave (usuario = `device_id`, token propio por nodo, sin anónimos) y ACL
(`infra-local/emqx/acl.conf`): cada nodo solo publica en `farmacias/<su_id>/lecturas|eventos` y solo lee
`farmacias/<su_id>/ack`; el backend lee todo y solo publica acuses; lo demás se deniega
(`authorization.no_match = deny`).

## Resultados (`python scripts/smoke_test.py`)
```
[OK ] nodo publica QoS1 en su tópico y backend lo recibe
[OK ] backend publica acuse y el nodo lo recibe
[OK ] ACL: publicar en tópico ajeno es descartado
[OK ] token inválido rechazado  rechazado: MqttConnectError
[OK ] usuario inexistente rechazado  rechazado: MqttConnectError
[OK ] ACL: nodo no puede suscribirse a lecturas de todos  suback=Unspecified error
```

## Logs de EMQX (`docker compose logs emqx`)
```
clientid: FARM-01-CDL, msg: terminate, username: FARM-01-CDL, reason: {shutdown,bad_username_or_password}
tag: AUTHZ, clientid: FARM-01-CDL, msg: authorization_permission_allowed, topic: farmacias/FARM-01-CDL/lecturas, action: PUBLISH(Q1,R0), source: file
tag: AUTHZ, clientid: FARM-01-CDL, msg: authorization_permission_allowed, topic: farmacias/FARM-01-CDL/ack, action: SUBSCRIBE(Q1), source: file
```
El primero corresponde a un nodo real con un token antiguo en NVS: el broker lo rechazó hasta reaprovisionarlo.
