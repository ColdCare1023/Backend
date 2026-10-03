from src.infrastructure.security.password_hasher import hash_password, verify_password


def test_hash_password_no_almacena_texto_plano():
    hashed = hash_password("MiPasswordSegura123")
    assert hashed != "MiPasswordSegura123"


def test_verify_password_acepta_password_correcto():
    hashed = hash_password("MiPasswordSegura123")
    assert verify_password("MiPasswordSegura123", hashed) is True


def test_verify_password_rechaza_password_incorrecto():
    hashed = hash_password("MiPasswordSegura123")
    assert verify_password("otro-password", hashed) is False


def test_hashes_del_mismo_password_son_distintos_por_el_salt():
    a = hash_password("MiPasswordSegura123")
    b = hash_password("MiPasswordSegura123")
    assert a != b
