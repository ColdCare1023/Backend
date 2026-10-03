"""Hallazgo S-04: longitud mínima de la clave HMAC (RFC 7518 §3.2).

La comprobación existía solo para `environment="production"`. En desarrollo y
en la propia suite de pruebas se aceptaban claves de 15 bytes en silencio —
el único rastro era un `InsecureKeyLengthWarning` genérico de PyJWT, fácil de
confundir con ruido de librerías. Una clave que se tolera en desarrollo acaba
copiándose al despliegue.
"""

import warnings

import pytest

from src.infrastructure.config import (
    LONGITUD_MINIMA_CLAVE_JWT,
    ClaveJWTDebilWarning,
    Settings,
)

CLAVE_CONFORME = "c" * LONGITUD_MINIMA_CLAVE_JWT


def test_clave_corta_fuera_de_produccion_emite_aviso_propio():
    with pytest.warns(ClaveJWTDebilWarning, match="15 bytes"):
        Settings(environment="development", jwt_secret_key="clave-de-prueba")


def test_clave_corta_en_pruebas_tambien_avisa():
    with pytest.warns(ClaveJWTDebilWarning):
        Settings(environment="test", jwt_secret_key="corta")


def test_clave_conforme_no_emite_ningun_aviso():
    with warnings.catch_warnings():
        warnings.simplefilter("error", ClaveJWTDebilWarning)
        Settings(environment="development", jwt_secret_key=CLAVE_CONFORME)


def test_la_longitud_se_mide_en_BYTES_no_en_caracteres():
    """31 caracteres no ASCII superan 32 bytes; 31 ASCII no. La firma HMAC
    opera sobre bytes, así que contar caracteres sobreestimaría la clave."""
    with pytest.warns(ClaveJWTDebilWarning):
        Settings(environment="development", jwt_secret_key="a" * 31)

    with warnings.catch_warnings():
        warnings.simplefilter("error", ClaveJWTDebilWarning)
        Settings(environment="development", jwt_secret_key="ñ" * 31)  # 62 bytes


def test_en_produccion_la_clave_corta_impide_arrancar():
    with pytest.raises(ValueError, match="al menos 32 bytes"):
        Settings(
            environment="production",
            jwt_secret_key="a" * (LONGITUD_MINIMA_CLAVE_JWT - 1),
            allowed_hosts=["api.ejemplo.pe"],
            cors_origins=["https://app.ejemplo.pe"],
            database_url="postgresql+asyncpg://u:p@db/real",
            mqtt_enabled=False,
        )
