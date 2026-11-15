# Hash canónico de la cadena de trazabilidad (HU-24, HU-25, HU-26)

Este documento fija cómo se calcula `hash_actual` de cada eslabón de
`traceability_records`, para que un auditor pueda recalcularlo fuera del
sistema. La implementación es `src/domain/value_objects/hash_encadenado.py`.
La prueba `tests/unit/test_hash_canonico_documentado.py` ejecuta el código de
la sección «Recalcular un eslabón» y lo compara con esa implementación, así que
este documento no puede quedar desalineado sin que falle la suite.

## Qué entra en el hash

Cada eslabón guarda `chain_id`, `chain_seq`, `previous_hash`, `timestamp`,
`payload`, `hash_actual` y `hash_version`. El hash se calcula sobre cuatro
datos del evento:

| Dato | Valor |
|---|---|
| `chain_id` | `device_id` si el evento es de un dispositivo (lectura, alerta, acción correctiva, conectividad, credencial, broker); `SISTEMA` si no (login, usuarios, checklist, reportes, modelo IA). Las cadenas nunca se mezclan (HU-25). |
| `previous_hash` | `hash_actual` del eslabón anterior de la misma `chain_id` (mayor `chain_seq`). El primero de cada cadena usa la semilla `GENESIS_HASH`. |
| `timestamp` | Instante del evento en su forma canónica (abajo). |
| `payload` | El diccionario del evento tal como se guarda en la columna `payload`. |

## Semilla inicial

```
GENESIS_HASH = "0" * 64
```

Sesenta y cuatro caracteres `0`. Es el `previous_hash` del eslabón con
`chain_seq = 1` de cada `chain_id` (HU-25 esc. 2). La verificación de una
cadena completa arranca de este valor; la verificación por periodo (HU-37)
arranca del `hash_actual` del último eslabón anterior al periodo (ancla).

## Timestamp canónico

`timestamp_canonico(valor)`:

1. Si el `datetime` no tiene zona horaria (SQLite la pierde), se interpreta como UTC.
2. Se convierte a UTC.
3. Se escribe con `datetime.isoformat()`: `AAAA-MM-DDTHH:MM:SS.ffffff+00:00`.
   Los microsegundos se omiten cuando valen 0 (`2026-11-14T15:04:05+00:00`).

Ejemplo: `2026-11-14T10:04:05.123456-05:00` → `2026-11-14T15:04:05.123456+00:00`.

## Serialización JSON

Siempre `json.dumps(objeto, sort_keys=True, default=str)` de la biblioteca
estándar de Python, codificado en UTF-8. El resto de parámetros queda en su
valor por defecto, que también forma parte del formato:

| Parámetro | Valor | Efecto |
|---|---|---|
| `sort_keys` | `True` | Claves ordenadas en todos los niveles: el orden en que se armó el diccionario (o el que devuelve JSONB) no cambia el hash. |
| `default` | `str` | Tipos que JSON no conoce (`UUID`, `datetime`, `Decimal`) se escriben como `str(valor)`. |
| `separators` | `(", ", ": ")` | Un espacio tras cada coma y cada dos puntos. |
| `ensure_ascii` | `True` | Lo que no es ASCII se escapa como `\uXXXX` (`°C` → `°C`). |
| `indent` | `None` | Una sola línea. |
| `allow_nan` | `True` | No aplica en la práctica: el contrato rechaza `NaN` e `Infinity` (`allow_inf_nan=False`). |

`null`, cadena vacía y clave ausente son tres cosas distintas y se conservan
así (HU-24 esc. 4): `{"a": null}`, `{"a": ""}` y `{}` dan tres hashes
distintos, y cada uno se recalcula igual después de pasar por JSONB.

## Fórmulas por versión (`hash_version`)

### v1 (legado, sin `chain_id`)

```
hash_actual = SHA-256( previous_hash + timestamp + json.dumps(payload, sort_keys=True, default=str) )
```

Concatenación directa de tres cadenas, sin separadores. **No incluye
`chain_id`**: v1 es anterior a las cadenas por unidad monitoreada (HU-25) y
había una sola cadena global. Los eslabones v1 que existan se verifican con
esta fórmula y no se recalculan (un registro de auditoría inmutable no
reescribe su hash). La redacción del backlog («v1 y v2 sobre
canonical({chain_id, …})») describe v2; para v1 rige esta fórmula.

Para saber si una base tiene eslabones v1 (y, por tanto, si la nota anterior
aplica a la evidencia real), basta con esta consulta de solo lectura:

```sql
SELECT hash_version, count(*) FROM traceability_records GROUP BY 1 ORDER BY 1;
```

Si no aparece la versión 1, toda la evidencia de esa base sigue la fórmula
del backlog (v2 o v3). Los eslabones nuevos nunca son v1.

### v2 (SHA-256 sin clave)

```
canonical = json.dumps({"chain_id": chain_id, "previous_hash": previous_hash,
                        "timestamp": timestamp, "payload": payload},
                       sort_keys=True, default=str)
hash_actual = SHA-256(canonical)
```

Solo se emite fuera de producción, cuando `TRAZABILIDAD_HMAC_KEY` está vacía.

### v3 (HMAC-SHA256 con clave externa, BE-007)

```
hash_actual = HMAC-SHA256(clave = TRAZABILIDAD_HMAC_KEY codificada en UTF-8, mensaje = canonical)
```

El mismo `canonical` que v2. Es la versión de todos los eslabones nuevos en
producción. Con SHA-256 sin clave, quien pudiera escribir en la base podía
alterar un registro y recalcular todos los posteriores; con HMAC necesita
además la clave, que no está en la base.

El hash se escribe en hexadecimal en minúsculas (64 caracteres). La versión de
la fórmula va en cada eslabón (`hash_version`) y la elige el eslabón, no la
cadena: el enlace `previous_hash → hash_actual` entre un eslabón v2 y el v3
siguiente es válido porque `previous_hash` es el `hash_actual` almacenado.

## Recalcular un eslabón

Solo biblioteca estándar. `clave` es `None` para v1/v2 y los bytes de
`TRAZABILIDAD_HMAC_KEY` para v3.

```python
import hashlib
import hmac
import json
from datetime import datetime, timezone

GENESIS_HASH = "0" * 64


def timestamp_canonico(valor: datetime) -> str:
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(timezone.utc).isoformat()


def recalcular(version: int, chain_id: str, previous_hash: str, timestamp: str, payload: dict,
               clave: bytes | None = None) -> str | None:
    if version == 1:
        contenido = previous_hash + timestamp + json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(contenido.encode("utf-8")).hexdigest()
    canonical = json.dumps(
        {"chain_id": chain_id, "previous_hash": previous_hash, "timestamp": timestamp, "payload": payload},
        sort_keys=True, default=str,
    ).encode("utf-8")
    if version == 2:
        return hashlib.sha256(canonical).hexdigest()
    if not clave:
        return None  # v3 sin clave: «no concluida», nunca «íntegra» (HU-26 esc. 1)
    return hmac.new(clave, canonical, hashlib.sha256).hexdigest()
```

Para verificar una cadena: ordenar sus eslabones por `chain_seq`, comprobar
que el primero tiene `previous_hash == GENESIS_HASH` (o el ancla), que cada
`previous_hash` es el `hash_actual` del anterior y que
`recalcular(...)` coincide con `hash_actual` usando
`timestamp_canonico(timestamp)` del eslabón. Es lo que hace
`VerificarIntegridadRegistroUseCase` (`/api/trazabilidad/verificar`).

## Gestión de la clave `TRAZABILIDAD_HMAC_KEY`

- **Dónde vive:** solo en las variables del servicio (Railway → Variables). No
  está en la base de datos, en el repositorio ni en la imagen.
- **Requisitos (el arranque falla en producción si no se cumplen):** al menos
  32 bytes, distinta de `JWT_SECRET_KEY` y de `CODIGOS_HMAC_KEY`, y no puede
  ser un valor publicado en el historial del repositorio.
- **Generarla:** `python -c "import secrets; print(secrets.token_hex(32))"`.
- **Respaldo:** guardarla fuera de Railway (gestor de contraseñas del equipo).
  Sin ella, los eslabones v3 no se pueden verificar: la verificación los
  informa como `no_concluida`, nunca como íntegros.
- **Rotación:** el sistema usa una sola clave para emitir y verificar. Cambiarla
  hace que los eslabones v3 anteriores dejen de coincidir con la clave nueva
  (se verían como alterados). No se rota salvo compromiso; si hay que hacerlo,
  se conserva la clave anterior para verificar los eslabones emitidos con ella
  y se documenta el `chain_seq` de corte de cada cadena en el control de cambios.
- **Si se filtra:** quien tenga la clave y acceso de escritura a la base podría
  recalcular la cadena. Se trata como incidente: verificar todas las cadenas con
  la clave vigente, archivar el resultado y rotar según el punto anterior.
