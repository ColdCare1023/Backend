"""Crea el primer administrador de una instalación nueva (H9, despliegue en Railway).

Uso (dentro del contenedor, p. ej. `railway ssh`, o con `railway connect` como túnel):

    python -m scripts.crear_admin

Pide nombre, correo y contraseña por consola (la contraseña no se muestra ni se
guarda en ningún archivo). Aplica la misma política de contraseñas que la API
(`PasswordNueva`) y se niega a correr si ya existe un administrador activo: es
solo para el arranque. Las demás cuentas se crean desde la interfaz con ese
administrador. Nunca ejecutar `seed_dev` en producción.

Variables opcionales para uso no interactivo (CI, scripts): ADMIN_NOMBRE,
ADMIN_EMAIL, ADMIN_PASSWORD. Preferir la entrada interactiva.
"""

import asyncio
import getpass
import os

from pydantic import EmailStr, TypeAdapter, ValidationError

from src.domain.entities.usuario import Usuario
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from src.infrastructure.database.session import _session_factory
from src.infrastructure.security.password_hasher import hash_password
from src.interface.api.schemas import PasswordNueva


def _pedir_nombre() -> str:
    nombre = (os.environ.get("ADMIN_NOMBRE") or input("Nombre completo: ")).strip()
    if not 1 <= len(nombre) <= 120:
        raise SystemExit("El nombre debe tener entre 1 y 120 caracteres.")
    return nombre


def _pedir_email() -> str:
    crudo = (os.environ.get("ADMIN_EMAIL") or input("Correo: ")).strip()
    try:
        return str(TypeAdapter(EmailStr).validate_python(crudo)).lower()
    except ValidationError as error:
        raise SystemExit(f"Correo inválido: {error.errors()[0]['msg']}") from None


def _pedir_password() -> str:
    clave = os.environ.get("ADMIN_PASSWORD")
    interactivo = clave is None
    if interactivo:
        clave = getpass.getpass("Contraseña: ")
    try:
        validada = TypeAdapter(PasswordNueva).validate_python(clave)
    except ValidationError as error:
        raise SystemExit(f"Contraseña no válida: {error.errors()[0]['msg']}") from None
    if interactivo and getpass.getpass("Repita la contraseña: ") != validada:
        raise SystemExit("Las contraseñas no coinciden.")
    return validada


async def main() -> None:
    async with _session_factory() as session:
        repo = SQLAlchemyUsuarioRepository(session)
        if await repo.contar_administradores_activos() > 0:
            raise SystemExit("Ya existe un administrador activo: este script es solo para el primero.")
        nombre, email = _pedir_nombre(), _pedir_email()
        if await repo.obtener_por_email(email) is not None:
            raise SystemExit(f"Ya existe una cuenta con el correo {email}.")
        password = _pedir_password()
        await repo.agregar(
            Usuario(nombre=nombre, email=email, password_hash=hash_password(password), rol=Rol.ADMINISTRADOR)
        )
        await session.commit()
    print(f"Administrador creado: {email}. Inicie sesión y acepte la política de privacidad.")


if __name__ == "__main__":
    asyncio.run(main())
