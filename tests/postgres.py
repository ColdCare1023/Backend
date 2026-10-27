"""B-7/B-9: PostgreSQL real para las pruebas que SQLite no puede representar
(triggers de solo anexado, `ON CONFLICT DO NOTHING`, bloqueos advisory).

Orden de preferencia:
1. `TEST_POSTGRES_URL` (servicio PostgreSQL del CI, p. ej.
   `postgresql://postgres:postgres@localhost:5432/postgres`): se crea una base
   temporal por módulo y se borra al terminar.
2. `pgserver` (PostgreSQL embebido de requirements-dev), si está instalado.
3. Ninguno: las pruebas se omiten con un motivo explícito. En el CI la
   variable siempre está definida, así que allí nunca se omiten.
"""

import asyncio
import os
import pathlib
import tempfile
import uuid
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]


def _con_base(url: str, base: str) -> str:
    partes = urlsplit(url)
    return urlunsplit(partes._replace(path=f"/{base}"))


@contextmanager
def _servidor():
    """Devuelve el DSN libpq (`postgresql://…`) de una base vacía y exclusiva."""
    asyncpg = pytest.importorskip("asyncpg")
    base = f"prueba_{uuid.uuid4().hex[:12]}"
    externo = os.environ.get("TEST_POSTGRES_URL")
    if externo:
        async def _admin(sentencia: str) -> None:
            conexion = await asyncpg.connect(externo)
            try:
                await conexion.execute(sentencia)
            finally:
                await conexion.close()

        asyncio.run(_admin(f'CREATE DATABASE "{base}"'))
        try:
            yield _con_base(externo, base)
        finally:
            asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{base}" WITH (FORCE)'))
        return
    try:
        import pgserver
    except ImportError:
        pytest.skip("Sin PostgreSQL: defina TEST_POSTGRES_URL o instale pgserver")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as carpeta:
        servidor = pgserver.get_server(carpeta, cleanup_mode="stop")
        servidor.psql(f'CREATE DATABASE "{base}";')
        socket = servidor.get_uri().split("host=")[1]
        try:
            yield f"postgresql://postgres@/{base}?host={socket}"
        finally:
            servidor.cleanup()


def a_asyncpg(dsn: str) -> str:
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


@contextmanager
def base_migrada():
    """Base PostgreSQL exclusiva con `alembic upgrade head` aplicado."""
    from alembic.config import Config

    from alembic import command
    from src.infrastructure.config import get_settings

    with _servidor() as dsn:
        parche = pytest.MonkeyPatch()
        parche.setenv("DATABASE_URL", a_asyncpg(dsn))
        get_settings.cache_clear()
        try:
            config = Config(str(RAIZ / "alembic.ini"))
            config.set_main_option("script_location", str(RAIZ / "alembic"))
            command.upgrade(config, "head")
            yield dsn
        finally:
            parche.undo()
            get_settings.cache_clear()
