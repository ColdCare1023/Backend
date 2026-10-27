# ADR: reglas ACL de EMQX en Serverless (B-2, HU-10)

**Estado:** aceptada (26/10/2026). **Relacionada:** ADR-15 del repositorio IoT
(autenticación integrada de EMQX, usuarios en modo manual).

## Contexto

`infra-local/emqx/acl.conf` define el mínimo privilegio con el marcador
`${username}` (`farmacias/${username}/lecturas`, …). Ese archivo lo lee
solo el EMQX local de `infra-local`. En EMQX Cloud Serverless:

- la autorización se configura en la consola de la instancia (Access
  Control → Authorization), no con un archivo `acl.conf`;
- la API de administración de Serverless no expone endpoints de reglas ACL;
- el uso del marcador `${username}` en las reglas de Serverless no está
  confirmado en una instancia real.

## Decisión

1. En Serverless se cargan **reglas explícitas por dispositivo**, generadas por
   `python -m scripts.reglas_acl_emqx` desde los dispositivos activos (sin
   marcadores). El `acl.conf` queda solo para la pila local.
2. La acción por defecto de la autorización debe ser **deny** (lo que no
   coincide se rechaza), igual que `authorization.no_match = deny` en local.
   Serverless no tiene el selector de modo lista blanca: se consigue con una
   regla en **All Users**: `#`, *Publish & Subscribe*, **Deny**. Las reglas
   por usuario se evalúan antes que las de All Users, así que las de la tabla
   siguen valiendo (documentación de EMQX Cloud, «Authorization Mode»).
   Preparación completa del banco: `HU-44_banco_serverless.md`.
3. Cada alta, baja o reemplazo de un nodo exige actualizar sus cuatro reglas en
   la consola. Hasta confirmarlo, el dispositivo queda con `broker_pendiente`
   en el backend (HU-43/HU-44) y su telemetría se rechaza si la credencial fue
   revocada (revocación a nivel de aplicación).

## Reglas (forma)

| Usuario | Acción | Tópico | Permiso |
|---|---|---|---|
| `backend_service` | subscribe | `farmacias/+/lecturas`, `farmacias/+/eventos` | allow |
| `backend_service` | publish | `farmacias/+/ack`, `farmacias/+/ota` | allow |
| `<device_id>` | publish | `farmacias/<device_id>/lecturas`, `farmacias/<device_id>/eventos` | allow |
| `<device_id>` | subscribe | `farmacias/<device_id>/ack`, `farmacias/<device_id>/ota` | allow |
| (todos) | all | `#` | deny |

## Checklist de verificación en la instancia real (evidencia de HU-10)

- [ ] Las reglas del script cargadas para cada nodo activo y la acción por defecto en deny.
- [ ] El nodo publica en su `farmacias/<id>/lecturas` y recibe su `ack` (captura del log del nodo).
- [ ] Con las credenciales del nodo A, publicar en `farmacias/<B>/lecturas` es rechazado (captura del cliente MQTT o del log de EMQX).
- [ ] Con las credenciales del nodo A, suscribirse a `farmacias/<B>/ack` es rechazado.
- [ ] Probar si `${username}` funciona en una regla de prueba; si funciona, puede sustituir las reglas por dispositivo y se actualiza esta ADR.

## Consecuencias

Más trabajo manual por nodo (cuatro reglas), a cambio de no depender de una
funcionalidad no verificada. Con un plan Dedicated (API de ACL disponible) el
backend podría cargarlas solo; queda como trabajo futuro.
