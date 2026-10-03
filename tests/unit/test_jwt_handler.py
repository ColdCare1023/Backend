import time
from uuid import uuid4

import pytest

from src.domain.exceptions import CredencialesInvalidasError
from src.domain.value_objects.rol import Rol
from src.infrastructure.config import Settings
from src.infrastructure.security.jwt_handler import JWTHandler

# Claves conformes con RFC 7518 §3.2 (>= 32 bytes). Con claves más cortas
# PyJWT emitía InsecureKeyLengthWarning en cada prueba de esta suite.
CLAVE_PRUEBA = "clave-de-prueba-conforme-rfc7518-0123456789"
OTRA_CLAVE = "otra-clave-distinta-conforme-rfc7518-0123456789"


@pytest.fixture
def jwt_handler() -> JWTHandler:
    settings = Settings(jwt_secret_key=CLAVE_PRUEBA, jwt_access_token_expire_minutes=60)
    return JWTHandler(settings)


def test_crear_y_decodificar_token_devuelve_los_mismos_datos(jwt_handler):
    usuario_id = uuid4()
    token = jwt_handler.crear_token(usuario_id, "user@test.com", Rol.TECNICO)

    payload = jwt_handler.decodificar_token(token)

    assert payload.sub == usuario_id
    assert payload.email == "user@test.com"
    assert payload.rol == Rol.TECNICO


def test_token_expirado_lanza_credenciales_invalidas():
    settings = Settings(jwt_secret_key=CLAVE_PRUEBA, jwt_access_token_expire_minutes=0)
    handler = JWTHandler(settings)
    token = handler.crear_token(uuid4(), "user@test.com", Rol.TECNICO)

    time.sleep(1.1)

    with pytest.raises(CredencialesInvalidasError):
        handler.decodificar_token(token)


def test_token_con_secreto_incorrecto_lanza_credenciales_invalidas(jwt_handler):
    otro_handler = JWTHandler(Settings(jwt_secret_key=OTRA_CLAVE))
    token = otro_handler.crear_token(uuid4(), "user@test.com", Rol.TECNICO)

    with pytest.raises(CredencialesInvalidasError):
        jwt_handler.decodificar_token(token)


def test_token_malformado_lanza_credenciales_invalidas(jwt_handler):
    with pytest.raises(CredencialesInvalidasError):
        jwt_handler.decodificar_token("esto-no-es-un-jwt-valido")
