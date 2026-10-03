"""Verificación del ID token de Google (OpenID Connect).

Un ID token de Google es un JWT firmado con RS256 por una clave que Google
publica y rota. Verificarlo de verdad exige cuatro cosas, y saltarse cualquiera
convierte el inicio de sesión en un formulario donde el atacante escribe quién
dice ser:

1. **Firma** contra las claves públicas de Google (JWKS). Sin esto, un token
   fabricado a mano se aceptaría.
2. **`aud`** igual a NUESTRO client_id. Sin esto, un ID token emitido para otra
   aplicación cualquiera —de las millones que usan Google— serviría para entrar
   aquí.
3. **`iss`** de Google.
4. **`exp`**, que PyJWT valida por defecto.

Se apoya en `PyJWKClient`, que descarga el JWKS y lo cachea respetando la
rotación de claves, en vez de fijar una clave en el código.
"""

import asyncio

import jwt
from jwt import PyJWKClient

from src.application.use_cases.autenticar_con_google import (
    IdentidadGoogle,
    VerificadorTokenGoogle,
)
from src.domain.exceptions import CredencialesInvalidasError

# Google publica aquí las claves con las que firma los ID token.
_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"

# Google emite `iss` con y sin esquema según el flujo; ambos son legítimos.
_EMISORES_VALIDOS = ("accounts.google.com", "https://accounts.google.com")


class GoogleTokenVerifier(VerificadorTokenGoogle):
    def __init__(self, client_id: str, jwks_url: str = _JWKS_URL) -> None:
        if not client_id:
            raise ValueError(
                "GOOGLE_CLIENT_ID es obligatorio para habilitar el acceso con Google."
            )
        self._client_id = client_id
        # `PyJWKClient` cachea las claves; se crea una sola vez para no
        # descargar el JWKS en cada inicio de sesión.
        self._jwks = PyJWKClient(jwks_url, cache_keys=True)

    async def verificar(self, id_token: str) -> IdentidadGoogle:
        # PyJWKClient hace E/S de red bloqueante. Ejecutarla directamente
        # detendría el bucle de eventos y con él toda la API —incluido el flujo
        # SSE del dashboard— mientras dura la descarga.
        return await asyncio.to_thread(self._verificar_sincrono, id_token)

    def _verificar_sincrono(self, id_token: str) -> IdentidadGoogle:
        try:
            clave = self._jwks.get_signing_key_from_jwt(id_token)
            datos = jwt.decode(
                id_token,
                clave.key,
                algorithms=["RS256"],
                audience=self._client_id,
                issuer=_EMISORES_VALIDOS,
                options={"require": ["exp", "iat", "aud", "iss", "email"]},
            )
        except Exception as exc:
            # No se propaga el detalle de PyJWT: distinguir "firma inválida" de
            # "audiencia incorrecta" o "expirado" ayuda a quien sondea el
            # endpoint. El motivo real queda en la auditoría del router.
            raise CredencialesInvalidasError("No se pudo iniciar sesión con Google") from exc

        email = datos.get("email")
        if not email:
            raise CredencialesInvalidasError("No se pudo iniciar sesión con Google")

        return IdentidadGoogle(
            email=str(email).lower(),
            # Google lo envía como booleano, pero algunos flujos lo serializan
            # como cadena; sin normalizar, la cadena "false" sería verdadera.
            email_verificado=str(datos.get("email_verified", False)).lower() == "true",
        )
