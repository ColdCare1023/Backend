"""HU-24 esc. 4: un payload con campos nulos, vacíos o ausentes se serializa
según el esquema canónico sin convertirse entre null, vacío o ausencia, y el
hash calculado al crear coincide con el recalculado al verificar.

Se prueba la fórmula (v2 SHA-256 y v3 HMAC), el camino real de la aplicación
en SQLite y la ida y vuelta por JSONB de PostgreSQL, que es donde un `null`
podría perderse o un `""` convertirse."""

import asyncio
import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.application.use_cases.verificar_integridad_registro import VerificarIntegridadRegistroUseCase
from src.domain.value_objects.hash_encadenado import (
    GENESIS_HASH,
    HASH_FORMULA_VERSION_ACTUAL,
    HASH_FORMULA_VERSION_HMAC,
    HashEncadenado,
    hash_esperado,
)
from src.infrastructure.config import get_settings
from src.infrastructure.database.repositories.trazabilidad_repository import SQLAlchemyTrazabilidadRepository
from tests.postgres import a_asyncpg, base_migrada

CLAVE = "clave-hmac-hu24-de-pruebas-0123456789abcdef"
TS = "2026-11-14T20:00:00+00:00"
CASOS = {
    "nulo": {"a": 1, "b": None},
    "vacio": {"a": 1, "b": ""},
    "ausente": {"a": 1},
    "lista_vacia": {"a": 1, "b": []},
    "objeto_vacio": {"a": 1, "b": {}},
    "anidado": {"a": 1, "b": {"k": None, "l": [], "s": ""}},
}
TRIO = ("nulo", "vacio", "ausente")


def _hash(version: int, payload: dict) -> str:
    clave = CLAVE.encode() if version == HASH_FORMULA_VERSION_HMAC else None
    return hash_esperado(version, "FARM-HU24", GENESIS_HASH, TS, payload, clave)


@pytest.fixture(params=["v2", "v3"])
def version(request, monkeypatch):
    """v2 sin clave; v3 con TRAZABILIDAD_HMAC_KEY (la de producción)."""
    monkeypatch.setenv("TRAZABILIDAD_HMAC_KEY", CLAVE if request.param == "v3" else "")
    get_settings.cache_clear()
    yield HASH_FORMULA_VERSION_HMAC if request.param == "v3" else HASH_FORMULA_VERSION_ACTUAL
    get_settings.cache_clear()


# ── Fórmula ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("v", [HASH_FORMULA_VERSION_ACTUAL, HASH_FORMULA_VERSION_HMAC])
def test_nulo_vacio_y_ausente_dan_hashes_distintos(v):
    hashes = {nombre: _hash(v, CASOS[nombre]) for nombre in CASOS}
    assert len(set(hashes.values())) == len(CASOS)


@pytest.mark.parametrize("v", [HASH_FORMULA_VERSION_ACTUAL, HASH_FORMULA_VERSION_HMAC])
@pytest.mark.parametrize("nombre", list(CASOS))
def test_recalcular_tras_serializar_reproduce_el_hash(v, nombre):
    """Lo que se guarda es JSON: tras una ida y vuelta por texto, el
    recálculo coincide (null sigue null, "" sigue "", la clave ausente sigue
    ausente)."""
    original = CASOS[nombre]
    releido = json.loads(json.dumps(original))
    assert releido == original
    assert ("b" in releido) == ("b" in original)
    assert _hash(v, releido) == _hash(v, original)


def test_la_v2_es_la_formula_documentada():
    assert _hash(HASH_FORMULA_VERSION_ACTUAL, CASOS["nulo"]) == HashEncadenado.calcular_hash(
        "FARM-HU24", GENESIS_HASH, TS, {"a": 1, "b": None}
    )


# ── Camino de la aplicación (SQLite) ─────────────────────────────────────


async def test_el_camino_real_conserva_y_verifica_cada_caso(db_session_factory, version):
    async with db_session_factory() as session:
        caso = RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session))
        for nombre, payload in CASOS.items():
            await caso.execute("PRUEBA_HU24", payload, chain_id=f"HU24-{nombre}")
        await session.commit()

    async with db_session_factory() as session:
        repositorio = SQLAlchemyTrazabilidadRepository(session)
        for nombre, payload in CASOS.items():
            [registro] = await repositorio.listar_todos_ordenados(f"HU24-{nombre}")
            assert registro.payload == payload and ("b" in registro.payload) == ("b" in payload), nombre
            assert registro.hash_version == version
            resultado = await VerificarIntegridadRegistroUseCase(repositorio).execute(f"HU24-{nombre}")
            assert resultado.integra is True, nombre


# ── Ida y vuelta por JSONB (PostgreSQL) ──────────────────────────────────


@pytest.fixture(scope="module")
def dsn():
    with base_migrada() as url:
        yield url


def test_postgres_jsonb_no_confunde_nulo_vacio_ni_ausente(dsn, version):
    async def flujo():
        motor = create_async_engine(a_asyncpg(dsn))
        fabrica = async_sessionmaker(motor, expire_on_commit=False)
        sufijo = f"v{version}"
        try:
            hashes = {}
            for nombre, payload in CASOS.items():
                async with fabrica() as session:
                    registro = await RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session)).execute(
                        "PRUEBA_HU24", payload, chain_id=f"PG-{sufijo}-{nombre}"
                    )
                    await session.commit()
                    hashes[nombre] = registro.hash_actual
            leidos, integras = {}, {}
            for nombre in CASOS:
                async with fabrica() as session:  # sesión nueva: se lee lo que devolvió JSONB
                    repositorio = SQLAlchemyTrazabilidadRepository(session)
                    [registro] = await repositorio.listar_todos_ordenados(f"PG-{sufijo}-{nombre}")
                    leidos[nombre] = (registro.payload, registro.hash_actual, registro.hash_version)
                    integras[nombre] = (
                        await VerificarIntegridadRegistroUseCase(repositorio).execute(f"PG-{sufijo}-{nombre}")
                    ).integra
            return hashes, leidos, integras
        finally:
            await motor.dispose()

    hashes, leidos, integras = asyncio.run(flujo())

    for nombre, payload in CASOS.items():
        leido, hash_actual, hash_version = leidos[nombre]
        assert leido == payload, nombre
        assert ("b" in leido) == ("b" in payload), nombre
        assert hash_version == version
        assert hash_actual == hashes[nombre]
        assert integras[nombre] is True, nombre
    assert len({hashes[nombre] for nombre in TRIO}) == 3
