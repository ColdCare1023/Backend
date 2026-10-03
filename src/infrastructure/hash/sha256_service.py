from src.domain.value_objects.hash_encadenado import GENESIS_HASH, HashEncadenado


class SHA256TrazabilidadService:
    """Wrapper de infraestructura sobre el value object HashEncadenado (hashlib stdlib)."""

    @staticmethod
    def genesis() -> str:
        return GENESIS_HASH

    @staticmethod
    def encadenar(chain_id: str, previous_hash: str, timestamp_iso: str, payload: dict) -> HashEncadenado:
        return HashEncadenado.encadenar(chain_id, previous_hash, timestamp_iso, payload)

    @staticmethod
    def verificar_cadena(chain_id: str, registros: list[dict]) -> bool:
        """registros: lista ordenada (de UNA sola cadena) de dicts con
        timestamp, payload, previous_hash, hash_actual."""
        previous_hash = GENESIS_HASH
        for registro in registros:
            esperado = HashEncadenado.calcular_hash(chain_id, previous_hash, registro["timestamp"], registro["payload"])
            if esperado != registro["hash_actual"] or registro["previous_hash"] != previous_hash:
                return False
            previous_hash = registro["hash_actual"]
        return True
