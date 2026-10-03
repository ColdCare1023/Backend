from src.infrastructure.hash.sha256_service import SHA256TrazabilidadService

CHAIN_ID = "FARM-01"


def _construir_cadena_valida(n: int) -> list[dict]:
    registros = []
    previous_hash = SHA256TrazabilidadService.genesis()
    for i in range(n):
        payload = {"indice": i}
        timestamp = f"2026-01-01T00:0{i}:00+00:00"
        encadenado = SHA256TrazabilidadService.encadenar(CHAIN_ID, previous_hash, timestamp, payload)
        registros.append(
            {
                "timestamp": timestamp,
                "payload": payload,
                "previous_hash": encadenado.previous_hash,
                "hash_actual": encadenado.hash_actual,
            }
        )
        previous_hash = encadenado.hash_actual
    return registros


def test_verificar_cadena_valida_retorna_true():
    registros = _construir_cadena_valida(5)
    assert SHA256TrazabilidadService.verificar_cadena(CHAIN_ID, registros) is True


def test_verificar_cadena_vacia_es_valida():
    assert SHA256TrazabilidadService.verificar_cadena(CHAIN_ID, []) is True


def test_verificar_cadena_detecta_alteracion_de_un_registro_intermedio():
    registros = _construir_cadena_valida(5)
    registros[2]["payload"] = {"indice": 999}
    assert SHA256TrazabilidadService.verificar_cadena(CHAIN_ID, registros) is False


def test_verificar_cadena_detecta_registro_faltante():
    registros = _construir_cadena_valida(5)
    del registros[2]
    assert SHA256TrazabilidadService.verificar_cadena(CHAIN_ID, registros) is False


def test_verificar_cadena_detecta_chain_id_incorrecto():
    """HU-25: verificar la misma cadena de registros bajo un chain_id
    distinto al que se usó para encadenarla debe fallar."""
    registros = _construir_cadena_valida(3)
    assert SHA256TrazabilidadService.verificar_cadena("OTRA-CADENA", registros) is False
