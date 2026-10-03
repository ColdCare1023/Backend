# Evidencia — HU-07: Sincronización con acuse lógico — lado backend

Entorno: `infra-local/` (PostgreSQL 16, EMQX 5.8.6 con TLS 8883 y CA propia, backend FastAPI en Docker), nodo ESP32 DevKitC real con firmware ColdCare1023/IoT `develop` (v1.2.0) salvo donde se indique, frontend ColdCare1023/Frontend `main`. Fechas: 2–3 de octubre de 2026.

## Defectos corregidos
- El acuse se publicaba con `json.dumps` por defecto (`"clave": valor`); un parser por texto plano no lo
  reconocía y el nodo **nunca liberaba** su buffer. Ahora es JSON compacto.
- Con el contrato v1.0 el acuse incluye `boot_id`, `seq_no` y `status`, que es como el nodo correlaciona.

## Verificación
- `test_acuse_usa_json_compacto_para_el_parser_del_firmware`.
- Nodo real: en cada ciclo `[Red] 1 lectura(s) con acuse logico; quedan 0.`; tras un corte del broker el
  nodo reenvió su backlog en orden FIFO y lo borró solo después de PUBACK + acuse.
