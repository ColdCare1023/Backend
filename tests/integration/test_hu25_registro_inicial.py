"""HU-25 esc. 2 (registro inicial): el primer evento de un chain_id nuevo usa
como previous_hash la semilla predefinida y documentada (GENESIS_HASH = 64
ceros) y abre la cadena con chain_seq = 1; el siguiente encadena con él. Cada
chain_id empieza su propia secuencia aunque los eventos se intercalen."""

from datetime import datetime, timezone
from itertools import pairwise

import pytest

from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.value_objects.hash_encadenado import (
    GENESIS_HASH,
    HASH_FORMULA_VERSION_ACTUAL,
    HASH_FORMULA_VERSION_HMAC,
    hash_esperado,
    timestamp_canonico,
)
from src.infrastructure.config import get_settings
from src.infrastructure.database.repositories.trazabilidad_repository import SQLAlchemyTrazabilidadRepository
from tests.conftest import auth_header

CLAVE = "clave-hmac-hu25-de-pruebas-0123456789abcdef"


@pytest.fixture(params=["v2", "v3"])
def version(request, monkeypatch):
    monkeypatch.setenv("TRAZABILIDAD_HMAC_KEY", CLAVE if request.param == "v3" else "")
    get_settings.cache_clear()
    yield HASH_FORMULA_VERSION_HMAC if request.param == "v3" else HASH_FORMULA_VERSION_ACTUAL
    get_settings.cache_clear()


def test_la_semilla_es_la_documentada():
    assert GENESIS_HASH == "0" * 64


async def test_el_primer_eslabon_de_cada_cadena_nueva_parte_de_la_semilla(db_session_factory, version):
    orden = ["NUEVO-A", "NUEVO-B", "NUEVO-A", None, "NUEVO-B", "NUEVO-A"]  # None = cadena SISTEMA
    async with db_session_factory() as session:
        caso = RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session))
        for i, device_id in enumerate(orden):
            await caso.execute("PRUEBA_HU25", {"i": i}, device_id=device_id)
        await session.commit()

    clave = CLAVE.encode() if version == HASH_FORMULA_VERSION_HMAC else None
    async with db_session_factory() as session:
        repositorio = SQLAlchemyTrazabilidadRepository(session)
        for chain_id, esperados in (("NUEVO-A", 3), ("NUEVO-B", 2), ("SISTEMA", 1)):
            cadena = await repositorio.listar_todos_ordenados(chain_id)
            assert len(cadena) == esperados, chain_id
            primero = cadena[0]
            assert primero.previous_hash == GENESIS_HASH, chain_id
            assert primero.chain_seq == 1, chain_id
            assert [r.chain_seq for r in cadena] == list(range(1, esperados + 1))
            for anterior, siguiente in pairwise(cadena):
                assert siguiente.previous_hash == anterior.hash_actual
            for registro in cadena:
                assert registro.hash_version == version
                assert registro.hash_actual == hash_esperado(
                    version, chain_id, registro.previous_hash, timestamp_canonico(registro.timestamp),
                    registro.payload, clave,
                )


def test_la_primera_lectura_de_un_dispositivo_nuevo_abre_su_cadena(client, token_tecnico, token_auditor):
    respuesta = client.post(
        "/api/lecturas",
        json={
            "device_id": "FARM-HU25-NUEVO", "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "temperatura_interna": 5.0, "temperatura_ambiental": 21.0, "humedad_ambiental": 55.0,
            "apertura_refrigerador": False, "estado_conectividad": "online",
        },
        headers=auth_header(token_tecnico),
    )
    assert respuesta.status_code == 201

    cadena = client.get(
        "/api/trazabilidad", params={"chain_id": "FARM-HU25-NUEVO", "limite": 50}, headers=auth_header(token_auditor)
    ).json()
    primero = min(cadena, key=lambda r: r["chain_seq"])
    assert primero["chain_seq"] == 1
    assert primero["previous_hash"] == GENESIS_HASH
