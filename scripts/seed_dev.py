"""Crea usuarios de desarrollo para pruebas locales.

Uso: DATABASE_URL=sqlite+aiosqlite:///./dev.db python -m scripts.seed_dev
"""

import asyncio

from src.domain.entities.usuario import Usuario
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from src.infrastructure.database.session import _session_factory
from src.infrastructure.security.password_hasher import hash_password

USUARIOS_DEV = [
    ("Admin Demo", "admin@farmacia.demo.pe", "admin12345", Rol.ADMINISTRADOR),
    ("Farmaceutico Demo", "farmaceutico@farmacia.demo.pe", "farma12345", Rol.FARMACEUTICO),
    ("Tecnico Demo", "tecnico@farmacia.demo.pe", "tecni12345", Rol.TECNICO),
]


async def main() -> None:
    async with _session_factory() as session:
        repo = SQLAlchemyUsuarioRepository(session)
        for nombre, email, password, rol in USUARIOS_DEV:
            if await repo.obtener_por_email(email) is None:
                await repo.agregar(
                    Usuario(nombre=nombre, email=email, password_hash=hash_password(password), rol=rol)
                )
                print(f"Creado: {email} ({rol.value})")
            else:
                print(f"Ya existe: {email}")
        await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
