from passlib.context import CryptContext

# HU-39: Argon2id es el algoritmo que exige la historia explícitamente (ganador
# del Password Hashing Competition, resistente a ASIC/GPU). bcrypt queda
# habilitado solo para verificar hashes ya existentes de antes de este cambio
# — passlib los reconoce por su prefijo y los sigue validando correctamente,
# así ningún usuario existente queda con su contraseña invalidada.
_pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated=["bcrypt"])


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _pwd_context.verify(password, password_hash)
