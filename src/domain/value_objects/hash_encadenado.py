import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

GENESIS_HASH = "0" * 64

# v1: SHA-256(previous_hash + timestamp + json(payload)) — sin chain_id, una
# única cadena global (diseño previo a HU-24/25 del backlog de 54 HU).
# v2: SHA-256(canonical({chain_id, previous_hash, timestamp, payload})) — una
# cadena independiente por chain_id (HU-25), sin mezclar unidades monitoreadas.
#
# Los registros ya persistidos con v1 NO se recalculan retroactivamente: un
# rastro de auditoría inmutable no puede reescribir su propio hash histórico
# solo porque cambió el esquema. Cada registro guarda su hash_version y se
# verifica con la fórmula que le corresponde; el enlace previous_hash→hash_actual
# entre un registro v1 y el siguiente registro v2 de la misma cadena sigue
# siendo válido porque previous_hash es simplemente el hash_actual almacenado
# del registro anterior, sin importar con qué fórmula se produjo.
HASH_FORMULA_VERSION_ACTUAL = 2


def timestamp_canonico(valor: datetime) -> str:
    """Forma canónica del timestamp para hashear: siempre UTC con offset.

    Motivo: algunos motores (SQLite en tests/desarrollo) devuelven datetimes
    naive aunque la columna sea timezone-aware; sin canonicalizar, el hash
    calculado al crear el registro no coincidiría al verificar la cadena.
    """
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class HashEncadenado:
    """Vincula un registro de trazabilidad con el hash del registro anterior."""

    previous_hash: str
    hash_actual: str

    @staticmethod
    def calcular_hash_v1(previous_hash: str, timestamp: str, payload: dict) -> str:
        """Fórmula legada (sin chain_id). Solo para verificar registros
        anteriores a la migración de chain_id — nunca para registros nuevos."""
        contenido = previous_hash + timestamp + json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(contenido.encode("utf-8")).hexdigest()

    @staticmethod
    def calcular_hash(chain_id: str, previous_hash: str, timestamp: str, payload: dict) -> str:
        """HU-24: current_hash = SHA-256(canonical({chain_id, previous_hash, timestamp, payload}))."""
        canonical = {
            "chain_id": chain_id,
            "previous_hash": previous_hash,
            "timestamp": timestamp,
            "payload": payload,
        }
        contenido = json.dumps(canonical, sort_keys=True, default=str)
        return hashlib.sha256(contenido.encode("utf-8")).hexdigest()

    @classmethod
    def encadenar(cls, chain_id: str, previous_hash: str, timestamp: str, payload: dict) -> "HashEncadenado":
        hash_actual = cls.calcular_hash(chain_id, previous_hash, timestamp, payload)
        return cls(previous_hash=previous_hash, hash_actual=hash_actual)

    def verificar(self, chain_id: str, timestamp: str, payload: dict) -> bool:
        return self.hash_actual == self.calcular_hash(chain_id, self.previous_hash, timestamp, payload)
