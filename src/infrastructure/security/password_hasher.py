"""HU-39: Argon2id para contraseñas y tokens de dispositivo.

BE-021: se usa `argon2-cffi` directamente en vez de `passlib`, que no publica
versiones desde 2020 y obligaba a fijar `bcrypt<4.1`. El formato de los hashes
no cambia (cadena PHC `$argon2id$v=19$...`), así que los hashes ya guardados
por passlib se siguen verificando sin migración.

bcrypt queda solo para verificar hashes heredados (anteriores a HU-39); el
login los reemplaza por Argon2id en cuanto el usuario se autentica
(`necesita_rehash`).
"""

import bcrypt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_argon2 = PasswordHasher()

_PREFIJOS_BCRYPT = ("$2a$", "$2b$", "$2y$")
# bcrypt solo considera los primeros 72 bytes: una contraseña más larga no se
# compara contra un hash heredado (se truncaría en silencio).
_BCRYPT_MAX_BYTES = 72


def _es_bcrypt(password_hash: str) -> bool:
    return password_hash.startswith(_PREFIJOS_BCRYPT)


def hash_password(password: str) -> str:
    return _argon2.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """False ante contraseña incorrecta o hash irreconocible; nunca lanza."""
    if _es_bcrypt(password_hash):
        clave = password.encode("utf-8")
        if len(clave) > _BCRYPT_MAX_BYTES:
            return False
        try:
            return bcrypt.checkpw(clave, password_hash.encode("ascii"))
        except ValueError:
            return False
    try:
        return _argon2.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def necesita_rehash(password_hash: str) -> bool:
    """True si el hash es heredado (bcrypt) o usa parámetros Argon2 distintos
    de los actuales. Solo se puede rehashear con la contraseña en claro, es
    decir, tras un login correcto."""
    if _es_bcrypt(password_hash):
        return True
    try:
        return _argon2.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True
