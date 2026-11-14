"""Códigos de verificación sobre PostgreSQL real: emisiones simultáneas y el
índice único parcial de la migración 0038.

En SQLite las escrituras ya se serializan; aquí se comprueba que en READ
COMMITTED varias solicitudes de recuperación a la vez dejan UN solo código vivo
(bloqueo de la fila del usuario + índice único parcial) y que el tope de fallos
por cuenta suma los códigos anulados."""

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.application.use_cases.codigos_verificacion import PoliticaCodigos, ServicioCodigosVerificacion
from src.domain.entities.codigo_verificacion import PropositoCodigo
from src.domain.entities.usuario import Usuario
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.models import CodigoVerificacionModel
from src.infrastructure.database.repositories.codigo_verificacion_repository import (
    SQLAlchemyCodigoVerificacionRepository,
)
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from tests.postgres import a_asyncpg, base_migrada

N_CONCURRENTES = 6
CLAVE = b"clave-hmac-de-codigos-para-pruebas-0123456789"


@pytest.fixture(scope="module")
def dsn():
    with base_migrada() as url:
        yield url


async def _con_fabrica(dsn: str, corrutina):
    motor = create_async_engine(a_asyncpg(dsn), pool_size=N_CONCURRENTES + 2)
    try:
        return await corrutina(async_sessionmaker(motor, expire_on_commit=False))
    finally:
        await motor.dispose()


async def _usuario(fabrica, email: str) -> Usuario:
    async with fabrica() as session:
        usuario = await SQLAlchemyUsuarioRepository(session).agregar(
            Usuario(nombre="Ana", email=email, password_hash="x", rol=Rol.TECNICO)
        )
        await session.commit()
        return usuario


def _servicio(session, **politica) -> ServicioCodigosVerificacion:
    return ServicioCodigosVerificacion(SQLAlchemyCodigoVerificacionRepository(session), CLAVE, PoliticaCodigos(**politica))


def test_solicitudes_simultaneas_dejan_un_solo_codigo_vivo(dsn):
    async def flujo(fabrica):
        usuario = await _usuario(fabrica, "simultaneo@farmacia.pe")

        async def emitir():
            async with fabrica() as session:
                emitido = await _servicio(session).emitir(usuario.id, PropositoCodigo.RECUPERACION)
                await session.commit()
                return emitido

        emitidos = await asyncio.gather(*[emitir() for _ in range(N_CONCURRENTES)])
        async with fabrica() as session:
            filas = (await session.execute(
                select(CodigoVerificacionModel).where(CodigoVerificacionModel.usuario_id == usuario.id)
            )).scalars().all()
            vivos = [f for f in filas if f.usado_en is None]
            # El único código que vale es el de la fila viva.
            (vivo,) = [e for e in emitidos if e.registro.id == vivos[0].id] if len(vivos) == 1 else [None]
            valido = await _servicio(session).verificar_para_usuario(
                usuario.id, [PropositoCodigo.RECUPERACION], vivo.codigo
            )
            await session.commit()
        return len(filas), len(vivos), valido.valido

    filas, vivos, valido = asyncio.run(_con_fabrica(dsn, flujo))
    assert (filas, vivos, valido) == (N_CONCURRENTES, 1, True)


def test_el_indice_unico_parcial_rechaza_un_segundo_codigo_vivo(dsn):
    async def flujo(fabrica):
        usuario = await _usuario(fabrica, "indice@farmacia.pe")
        async with fabrica() as session:
            emitido = await _servicio(session).emitir(usuario.id, PropositoCodigo.LOGIN, con_desafio=True)
            session.add(
                CodigoVerificacionModel(
                    usuario_id=usuario.id, proposito="login", codigo_hash="y" * 64,
                    expira_en=emitido.registro.expira_en,
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()

    asyncio.run(_con_fabrica(dsn, flujo))


def test_el_tope_por_cuenta_suma_los_codigos_anulados(dsn):
    async def flujo(fabrica):
        usuario = await _usuario(fabrica, "tope@farmacia.pe")
        async with fabrica() as session:
            servicio = _servicio(session, max_intentos=5, max_fallos_cuenta=10)
            for _ in range(2):
                emitido = await servicio.emitir(usuario.id, PropositoCodigo.RECUPERACION)
                incorrecto = "000000" if emitido.codigo != "000000" else "111111"
                for _ in range(5):
                    await servicio.verificar_para_usuario(usuario.id, [PropositoCodigo.RECUPERACION], incorrecto)
            await session.commit()
            return await servicio.bloqueado(usuario.id, PropositoCodigo.ACTIVACION)

    assert asyncio.run(_con_fabrica(dsn, flujo)) is True
