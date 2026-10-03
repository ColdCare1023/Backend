from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from src.domain.value_objects.hash_encadenado import (
    HASH_FORMULA_VERSION_ACTUAL,
    HashEncadenado,
    timestamp_canonico,
)


@dataclass(slots=True)
class RegistroTrazabilidad:
    """Registro con hash SHA-256 encadenado que garantiza integridad verificable.

    HU-25: `chain_id` identifica la cadena independiente a la que pertenece
    este eslabón (por convención, el device_id de la unidad monitoreada, o
    "SISTEMA" para eventos no ligados a un dispositivo); `chain_seq` es su
    posición dentro de esa cadena. `hash_version` distingue la fórmula con la
    que se calculó hash_actual (ver hash_encadenado.py) — necesario porque los
    registros anteriores a la introducción de chain_id no se recalculan."""

    tipo_evento: str
    payload: dict
    timestamp: datetime
    hash_encadenado: HashEncadenado
    chain_id: str
    id: UUID | None = None
    chain_seq: int | None = None
    hash_version: int = HASH_FORMULA_VERSION_ACTUAL
    device_id: str | None = None
    usuario_id: UUID | None = None

    @property
    def previous_hash(self) -> str:
        return self.hash_encadenado.previous_hash

    @property
    def hash_actual(self) -> str:
        return self.hash_encadenado.hash_actual

    def verificar_integridad(self) -> bool:
        if self.hash_version >= 2:
            return self.hash_encadenado.verificar(self.chain_id, timestamp_canonico(self.timestamp), self.payload)
        return self.hash_encadenado.hash_actual == HashEncadenado.calcular_hash_v1(
            self.hash_encadenado.previous_hash, timestamp_canonico(self.timestamp), self.payload
        )
