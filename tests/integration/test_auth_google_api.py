"""RF-17 (método de acceso alternativo): inicio de sesión con Google.

Google verifica la IDENTIDAD; la AUTORIZACIÓN sigue siendo la tabla `users`.
Estas pruebas ejercitan el endpoint completo —bandera de habilitación, cuota,
lista de acceso, auditoría— sustituyendo únicamente la verificación
criptográfica del ID token, que exigiría credenciales reales y salir a la red.
"""

import pytest

from src.application.use_cases.autenticar_con_google import (
    IdentidadGoogle,
    VerificadorTokenGoogle,
)
from src.domain.exceptions import CredencialesInvalidasError
from src.domain.value_objects.rol import Rol
from src.infrastructure.config import get_settings
from src.infrastructure.database.repositories.audit_log_repository import (
    SQLAlchemyAuditLogRepository,
)
from src.infrastructure.security import google_verifier_provider

EMAIL = "brenda@farmacia.example.org"


class _VerificadorFalso(VerificadorTokenGoogle):
    """Devuelve la identidad que Google habría certificado, o falla como lo
    haría un token manipulado."""

    def __init__(self, identidad: IdentidadGoogle | None) -> None:
        self._identidad = identidad

    async def verificar(self, id_token: str) -> IdentidadGoogle:
        if self._identidad is None:
            raise CredencialesInvalidasError("No se pudo iniciar sesión con Google")
        return self._identidad


@pytest.fixture
def google_habilitado(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cliente-de-prueba.apps.googleusercontent.com")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    google_verifier_provider.establecer_verificador_google(None)


def _con_identidad(email: str = EMAIL, verificado: bool = True) -> None:
    google_verifier_provider.establecer_verificador_google(
        _VerificadorFalso(IdentidadGoogle(email=email, email_verificado=verificado))
    )


async def _acciones_auditadas(db_session_factory) -> list[str]:
    async with db_session_factory() as session:
        return [e["accion"] for e in await SQLAlchemyAuditLogRepository(session).listar(limite=50)]


async def test_usuario_provisionado_obtiene_token(client, crear_usuario, google_habilitado):
    await crear_usuario("Brenda Gamio", EMAIL, "password123", Rol.FARMACEUTICO)
    _con_identidad()

    response = client.post("/api/auth/google", json={"id_token": "token-de-google"})

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert len(body["access_token"]) > 0


async def test_correo_no_provisionado_se_rechaza(client, google_habilitado):
    """La allowlist es la tabla `users`: tener cuenta de Google no basta.

    Sin esto, cualquier persona con Gmail entraría a un sistema que custodia
    evidencia de cadena de frío farmacéutica."""
    _con_identidad(email="cualquiera@gmail.com")

    response = client.post("/api/auth/google", json={"id_token": "token-de-google"})

    assert response.status_code == 401


async def test_usuario_desactivado_no_puede_entrar_por_google(
    client, crear_usuario, google_habilitado, db_session_factory
):
    """HU-45: la desactivación debe cerrar TODAS las puertas. Si Google fuera
    un camino paralelo, dar de baja a alguien no lo dejaría fuera."""
    from src.infrastructure.database.repositories.usuario_repository import (
        SQLAlchemyUsuarioRepository,
    )

    usuario = await crear_usuario("Ex Empleado", "ex@farmacia.example.org", "password123", Rol.TECNICO)
    async with db_session_factory() as session:
        repo = SQLAlchemyUsuarioRepository(session)
        entidad = await repo.obtener_por_id(usuario.id)
        entidad.is_active = False
        await repo.actualizar(entidad)
        await session.commit()

    _con_identidad(email="ex@farmacia.example.org")
    response = client.post("/api/auth/google", json={"id_token": "token-de-google"})

    assert response.status_code == 401


async def test_correo_sin_verificar_se_rechaza(client, crear_usuario, google_habilitado):
    """Un correo no verificado por Google puede pertenecer a otra persona:
    aceptarlo permitiría suplantar a un usuario dado de alta."""
    await crear_usuario("Brenda Gamio", EMAIL, "password123", Rol.FARMACEUTICO)
    _con_identidad(verificado=False)

    response = client.post("/api/auth/google", json={"id_token": "token-de-google"})

    assert response.status_code == 401


async def test_token_invalido_se_rechaza(client, crear_usuario, google_habilitado):
    await crear_usuario("Brenda Gamio", EMAIL, "password123", Rol.FARMACEUTICO)
    google_verifier_provider.establecer_verificador_google(_VerificadorFalso(None))

    response = client.post("/api/auth/google", json={"id_token": "token-manipulado"})

    assert response.status_code == 401


async def test_el_rechazo_no_revela_si_el_correo_esta_dado_de_alta(
    client, crear_usuario, google_habilitado
):
    """Anti-enumeración: el mensaje de un correo ajeno al sistema y el de un
    token inválido deben ser indistinguibles, o el endpoint serviría para
    averiguar quién tiene acceso."""
    await crear_usuario("Brenda Gamio", EMAIL, "password123", Rol.FARMACEUTICO)

    _con_identidad(email="desconocido@gmail.com")
    ajeno = client.post("/api/auth/google", json={"id_token": "t"})

    google_verifier_provider.establecer_verificador_google(_VerificadorFalso(None))
    invalido = client.post("/api/auth/google", json={"id_token": "t"})

    assert ajeno.status_code == invalido.status_code == 401
    assert ajeno.json()["detail"] == invalido.json()["detail"]


async def test_deshabilitado_por_defecto(client, crear_usuario):
    """Sin GOOGLE_OAUTH_ENABLED el endpoint no existe: una instalación que no
    configuró Google no debe exponer una vía de acceso a medio configurar."""
    await crear_usuario("Brenda Gamio", EMAIL, "password123", Rol.FARMACEUTICO)
    _con_identidad()

    response = client.post("/api/auth/google", json={"id_token": "token-de-google"})

    assert response.status_code == 404


async def test_acceso_por_google_queda_en_la_bitacora(
    client, crear_usuario, google_habilitado, db_session_factory
):
    """RF-16: la auditoría debe permitir distinguir con qué método se abrió la
    sesión, no solo que alguien entró."""
    await crear_usuario("Brenda Gamio", EMAIL, "password123", Rol.FARMACEUTICO)
    _con_identidad()

    client.post("/api/auth/google", json={"id_token": "token-de-google"})

    assert "LOGIN_EXITOSO" in await _acciones_auditadas(db_session_factory)


async def test_intento_fallido_queda_auditado(client, google_habilitado, db_session_factory):
    _con_identidad(email="intruso@gmail.com")

    client.post("/api/auth/google", json={"id_token": "token-de-google"})

    assert "LOGIN_GOOGLE_FALLIDO" in await _acciones_auditadas(db_session_factory)
