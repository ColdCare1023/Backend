"""Inicio de sesión con Google restringido a usuarios ya provisionados.

Decisión de diseño — por qué NO se crean usuarios aquí
-----------------------------------------------------
El sistema custodia evidencia de cadena de frío farmacéutica. Si un inicio de
sesión con Google diera de alta al usuario, cualquier persona con una cuenta de
Google entraría, y además llegaría sin rol: el RBAC de tres roles (RF-17,
HU-41) dejaría de gobernar quién puede revisar alertas o exportar reportes.

Por eso Google actúa **solo como verificador de identidad**, nunca como fuente
de autorización. La lista de acceso es la propia tabla `users`: un
administrador da de alta el correo (HU-41) y solo entonces ese correo puede
entrar por Google. El rol, el estado activo (HU-45) y el consentimiento de la
Ley 29733 (HU-44) siguen viniendo de la base de datos, y el token que se emite
es exactamente el mismo JWT interno que el del login con contraseña, así que la
cadena de auditoría y la trazabilidad no distinguen el método de acceso más que
en el registro de la acción.
"""

from dataclasses import dataclass
from uuid import UUID

from src.application.use_cases.autenticar_usuario import ResultadoAutenticacion
from src.domain.exceptions import CredencialesInvalidasError
from src.domain.repositories.i_usuario_repository import IUsuarioRepository
from src.infrastructure.security.jwt_handler import JWTHandler


@dataclass(frozen=True, slots=True)
class IdentidadGoogle:
    """Lo único que se toma del token de Google. Deliberadamente mínimo: nada
    de nombre, foto ni identificador de Google se persiste, porque no aporta a
    la trazabilidad y ampliaría el tratamiento de datos personales bajo la Ley
    29733 sin justificación (HU-44)."""

    email: str
    email_verificado: bool


class VerificadorTokenGoogle:
    """Puerto de verificación del ID token. La implementación real vive en
    infraestructura; el caso de uso solo depende de esta forma, de modo que las
    pruebas no necesitan salir a la red ni credenciales de Google."""

    async def verificar(self, id_token: str) -> IdentidadGoogle:  # pragma: no cover - interfaz
        raise NotImplementedError


class AutenticarConGoogleUseCase:
    """RF-17 (método de acceso alternativo): autentica contra Google y autoriza
    contra la tabla `users`."""

    def __init__(
        self,
        usuario_repository: IUsuarioRepository,
        jwt_handler: JWTHandler,
        verificador: VerificadorTokenGoogle,
    ) -> None:
        self._usuario_repository = usuario_repository
        self._jwt_handler = jwt_handler
        self._verificador = verificador

    async def execute(self, id_token: str) -> ResultadoAutenticacion:
        identidad = await self._verificador.verificar(id_token)

        # Un correo no verificado por Google puede pertenecer a otra persona:
        # aceptarlo permitiría suplantar a un usuario dado de alta con solo
        # registrar ese correo en una cuenta de Google sin confirmar.
        if not identidad.email_verificado:
            raise CredencialesInvalidasError("No se pudo iniciar sesión con Google")

        usuario = await self._usuario_repository.obtener_por_email(identidad.email.lower())

        # Mismo mensaje para "no está en la lista", "está desactivado" y "el
        # token no vale": si el error distinguiera los casos, cualquiera podría
        # averiguar qué correos tienen acceso al sistema probando con los suyos.
        if usuario is None or not usuario.is_active:
            raise CredencialesInvalidasError("No se pudo iniciar sesión con Google")

        token = self._jwt_handler.crear_token(usuario.id, usuario.email, usuario.rol)
        return ResultadoAutenticacion(
            access_token=token,
            usuario_id=UUID(str(usuario.id)),
            require_privacy_consent=usuario.requiere_aceptar_privacidad,
        )
