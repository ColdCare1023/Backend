from src.domain.value_objects.hash_encadenado import GENESIS_HASH, HashEncadenado

CHAIN_ID = "FARM-01"


def test_encadenar_produce_hash_determinista():
    payload = {"temperatura": 4.2}
    a = HashEncadenado.encadenar(CHAIN_ID, GENESIS_HASH, "2026-01-01T00:00:00+00:00", payload)
    b = HashEncadenado.encadenar(CHAIN_ID, GENESIS_HASH, "2026-01-01T00:00:00+00:00", payload)
    assert a.hash_actual == b.hash_actual
    assert len(a.hash_actual) == 64


def test_hash_cambia_si_cambia_el_payload():
    a = HashEncadenado.encadenar(CHAIN_ID, GENESIS_HASH, "2026-01-01T00:00:00+00:00", {"temperatura": 4.2})
    b = HashEncadenado.encadenar(CHAIN_ID, GENESIS_HASH, "2026-01-01T00:00:00+00:00", {"temperatura": 4.3})
    assert a.hash_actual != b.hash_actual


def test_hash_cambia_si_cambia_previous_hash():
    payload = {"temperatura": 4.2}
    a = HashEncadenado.encadenar(CHAIN_ID, GENESIS_HASH, "2026-01-01T00:00:00+00:00", payload)
    b = HashEncadenado.encadenar(CHAIN_ID, a.hash_actual, "2026-01-01T00:00:00+00:00", payload)
    assert a.hash_actual != b.hash_actual


def test_hash_cambia_si_cambia_chain_id():
    """HU-25: la misma cadena de argumentos bajo un chain_id distinto (otra
    unidad monitoreada) debe producir un hash distinto — de lo contrario un
    registro podría "trasplantarse" de una cadena a otra sin que se note."""
    payload = {"temperatura": 4.2}
    a = HashEncadenado.encadenar("FARM-01", GENESIS_HASH, "2026-01-01T00:00:00+00:00", payload)
    b = HashEncadenado.encadenar("FARM-02", GENESIS_HASH, "2026-01-01T00:00:00+00:00", payload)
    assert a.hash_actual != b.hash_actual


def test_verificar_detecta_alteracion_del_payload():
    registro = HashEncadenado.encadenar(CHAIN_ID, GENESIS_HASH, "2026-01-01T00:00:00+00:00", {"temperatura": 4.2})
    assert registro.verificar(CHAIN_ID, "2026-01-01T00:00:00+00:00", {"temperatura": 4.2}) is True
    assert registro.verificar(CHAIN_ID, "2026-01-01T00:00:00+00:00", {"temperatura": 999.0}) is False


def test_orden_de_claves_en_payload_no_afecta_el_hash():
    a = HashEncadenado.encadenar(CHAIN_ID, GENESIS_HASH, "t", {"a": 1, "b": 2})
    b = HashEncadenado.encadenar(CHAIN_ID, GENESIS_HASH, "t", {"b": 2, "a": 1})
    assert a.hash_actual == b.hash_actual


def test_calcular_hash_v1_legado_no_incluye_chain_id():
    """Los registros previos a la introducción de chain_id (hash_version=1)
    deben seguir siendo verificables con la fórmula legada exacta, sin
    recalcularse — ver docstring de hash_version en el módulo."""
    payload = {"temperatura": 4.2}
    v1 = HashEncadenado.calcular_hash_v1(GENESIS_HASH, "2026-01-01T00:00:00+00:00", payload)
    v2_con_chain = HashEncadenado.calcular_hash(CHAIN_ID, GENESIS_HASH, "2026-01-01T00:00:00+00:00", payload)
    assert v1 != v2_con_chain
