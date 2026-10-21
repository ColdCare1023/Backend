import bcrypt

from src.infrastructure.security.password_hasher import hash_password, necesita_rehash, verify_password


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


# BE-021: sin passlib, los hashes que passlib guardó deben seguir validando.
# Generados con passlib 1.7.4 (CryptContext(["argon2", "bcrypt"])) para la
# contraseña de prueba "Legado-Passlib-2025".
_LEGADO = "Legado-Passlib-2025"
_ARGON2_DE_PASSLIB = "$argon2id$v=19$m=65536,t=3,p=4$wDinNMY4h3AupVRK6T1nTA$MdUNxkZ5HDwdLgUYuGMGhGb+/vXVR81S0TWuD8OFHiM"
_BCRYPT_DE_PASSLIB = "$2b$12$Obzc4GZEo4ykZFhRuZ3g4OptiLBQiHrIcEojTaWv4ohc9QoqtE9Ku"


def test_hash_nuevo_es_argon2id():
    assert hash_password("MiPasswordSegura123").startswith("$argon2id$")


def test_verifica_hashes_argon2_generados_por_passlib():
    assert verify_password(_LEGADO, _ARGON2_DE_PASSLIB) is True
    assert verify_password("otra", _ARGON2_DE_PASSLIB) is False
    assert necesita_rehash(_ARGON2_DE_PASSLIB) is False


def test_verifica_hashes_bcrypt_heredados_y_pide_rehash():
    assert verify_password(_LEGADO, _BCRYPT_DE_PASSLIB) is True
    assert verify_password("otra", _BCRYPT_DE_PASSLIB) is False
    assert necesita_rehash(_BCRYPT_DE_PASSLIB) is True


def test_bcrypt_heredado_no_trunca_contrasenas_largas():
    larga = "A1" + "x" * 80
    hash_truncado = bcrypt.hashpw(larga.encode()[:72], bcrypt.gensalt(rounds=4)).decode()

    assert verify_password(larga, hash_truncado) is False


def test_hash_irreconocible_no_lanza():
    assert verify_password("x", "no-es-un-hash") is False
    assert necesita_rehash("no-es-un-hash") is True
