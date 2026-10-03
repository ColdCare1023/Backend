"""RF-17 (acceso alternativo): verificación real del ID token de Google.

Se firma con un par RSA propio y se sirve su clave pública como si fuera el
JWKS de Google, de modo que se ejercita el mismo camino de `jwt.decode` que en
producción sin depender de la red.
"""

from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from src.domain.exceptions import CredencialesInvalidasError
from src.infrastructure.security.google_token_verifier import GoogleTokenVerifier

CLIENT_ID = "123456789.apps.googleusercontent.com"

_CLAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _emitir(**overrides) -> str:
    ahora = datetime.now(tz=timezone.utc)
    payload = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "email": "Farmaceutico@Farmacia.Example.Org",
        "email_verified": True,
        "iat": ahora,
        "exp": ahora + timedelta(hours=1),
    }
    payload.update(overrides)
    return pyjwt.encode(payload, _CLAVE, algorithm="RS256", headers={"kid": "clave-de-prueba"})


class _JWKSFalso:
    """Sustituye a PyJWKClient: devuelve siempre nuestra clave pública."""

    def get_signing_key_from_jwt(self, token):
        return type("Clave", (), {"key": _CLAVE.public_key()})()


@pytest.fixture
def verificador() -> GoogleTokenVerifier:
    v = GoogleTokenVerifier(CLIENT_ID)
    v._jwks = _JWKSFalso()
    return v


def test_client_id_vacio_es_un_error_de_configuracion():
    """Sin client_id no se puede validar `aud`, así que el verificador no debe
    poder construirse: valdría cualquier ID token de Google."""
    with pytest.raises(ValueError, match="GOOGLE_CLIENT_ID"):
        GoogleTokenVerifier("")


@pytest.mark.asyncio
async def test_token_valido_devuelve_la_identidad_normalizada(verificador):
    identidad = await verificador.verificar(_emitir())

    assert identidad.email == "farmaceutico@farmacia.example.org"
    assert identidad.email_verificado is True


@pytest.mark.asyncio
async def test_token_emitido_para_otra_aplicacion_se_rechaza(verificador):
    """El control central: sin comprobar `aud`, el ID token de cualquiera de
    los millones de aplicaciones que usan Google serviría para entrar aquí."""
    with pytest.raises(CredencialesInvalidasError):
        await verificador.verificar(_emitir(aud="otra-app.apps.googleusercontent.com"))


@pytest.mark.asyncio
async def test_token_de_un_emisor_que_no_es_google_se_rechaza(verificador):
    with pytest.raises(CredencialesInvalidasError):
        await verificador.verificar(_emitir(iss="https://accounts.atacante.example"))


@pytest.mark.asyncio
async def test_token_expirado_se_rechaza(verificador):
    ayer = datetime.now(tz=timezone.utc) - timedelta(days=1)
    with pytest.raises(CredencialesInvalidasError):
        await verificador.verificar(_emitir(exp=ayer, iat=ayer - timedelta(hours=1)))


@pytest.mark.asyncio
async def test_token_firmado_con_otra_clave_se_rechaza(verificador):
    otra = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ahora = datetime.now(tz=timezone.utc)
    token = pyjwt.encode(
        {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "email": "intruso@example.org",
            "iat": ahora,
            "exp": ahora + timedelta(hours=1),
        },
        otra,
        algorithm="RS256",
    )

    with pytest.raises(CredencialesInvalidasError):
        await verificador.verificar(token)


@pytest.mark.asyncio
async def test_token_sin_email_se_rechaza(verificador):
    with pytest.raises(CredencialesInvalidasError):
        await verificador.verificar(_emitir(email=""))


@pytest.mark.asyncio
async def test_email_verified_como_cadena_false_no_cuenta_como_verificado(verificador):
    """Algunos flujos serializan el booleano como cadena; sin normalizar, la
    cadena "false" sería verdadera por ser no vacía."""
    identidad = await verificador.verificar(_emitir(email_verified="false"))

    assert identidad.email_verificado is False


@pytest.mark.asyncio
async def test_el_mensaje_de_error_no_distingue_el_motivo_del_rechazo(verificador):
    """OWASP WSTG: mensajes distintos por causa permitirían sondear el endpoint
    para averiguar qué falla exactamente."""
    mensajes = set()
    for token in (_emitir(aud="otra"), _emitir(iss="https://malo.example"), "no-es-un-jwt"):
        try:
            await verificador.verificar(token)
        except CredencialesInvalidasError as exc:
            mensajes.add(str(exc))

    assert len(mensajes) == 1
