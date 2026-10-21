"""Crea usuarios de desarrollo para pruebas locales.

Uso: DATABASE_URL=sqlite+aiosqlite:///./dev.db python -m scripts.seed_dev

BE-023: las contraseñas ya no están en el código. Cada usuario toma la suya de
la variable de entorno indicada (infra-local/scripts/init.sh las genera en
infra-local/.env); si no está definida, se genera una aleatoria y se muestra
una sola vez, al crear el usuario. El script se niega a correr con
ENVIRONMENT=production.
"""

import asyncio
import os
import secrets
import string

from src.domain.entities.usuario import Usuario
from src.domain.value_objects.rol import Rol
from src.infrastructure.config import get_settings
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from src.infrastructure.database.session import _session_factory
from src.infrastructure.security.password_hasher import hash_password

# (nombre, email, variable de entorno con la contraseña, rol). Los scripts de
# infra-local leen esta tabla para saber qué variable consultar.
USUARIOS_DEV = [
    ("Admin Demo", "admin@farmacia.demo.pe", "SEED_ADMIN_PASSWORD", Rol.ADMINISTRADOR),
    ("Farmaceutico Demo", "farmaceutico@farmacia.demo.pe", "SEED_FARMACEUTICO_PASSWORD", Rol.FARMACEUTICO),
    ("Tecnico Demo", "tecnico@farmacia.demo.pe", "SEED_TECNICO_PASSWORD", Rol.TECNICO),
]


def generar_password() -> str:
    """Aleatoria y conforme a la política de la API (letras y dígitos, ≥10)."""
    cuerpo = secrets.token_urlsafe(15)
    return cuerpo + secrets.choice(string.ascii_letters) + secrets.choice(string.digits)


def password_de(variable: str) -> tuple[str, bool]:
    """Devuelve la contraseña y si se generó en esta ejecución."""
    valor = os.environ.get(variable)
    if valor:
        return valor, False
    return generar_password(), True


def exigir_entorno_no_productivo() -> None:
    if get_settings().environment == "production":
        raise SystemExit("seed_dev crea usuarios de prueba: no puede ejecutarse con ENVIRONMENT=production.")


async def main() -> None:
    exigir_entorno_no_productivo()
    async with _session_factory() as session:
        repo = SQLAlchemyUsuarioRepository(session)
        for nombre, email, variable, rol in USUARIOS_DEV:
            if await repo.obtener_por_email(email) is not None:
                print(f"Ya existe: {email}")
                continue
            password, generada = password_de(variable)
            await repo.agregar(
                Usuario(nombre=nombre, email=email, password_hash=hash_password(password), rol=rol)
            )
            if generada:
                print(f"Creado: {email} ({rol.value}) — contraseña generada (no se volverá a mostrar): {password}")
            else:
                print(f"Creado: {email} ({rol.value}) — contraseña de {variable}")
        await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
