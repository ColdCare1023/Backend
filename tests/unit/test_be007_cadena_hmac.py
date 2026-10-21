"""BE-007: eslabones v3 autenticados con HMAC-SHA256.

Sin la clave, alterar un registro y recalcular la cadena con SHA-256 ya no
produce una cadena que la verificación acepte."""

from datetime import datetime, timezone

import pytest

from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.application.use_cases.verificar_integridad_registro import VerificarIntegridadRegistroUseCase
from src.domain.value_objects.hash_encadenado import HashEncadenado, timestamp_canonico
from src.infrastructure.config import Settings, get_settings
from src.infrastructure.database.repositories.trazabilidad_repository import SQLAlchemyTrazabilidadRepository

CLAVE = "clave-hmac-de-trazabilidad-para-pruebas-000"


@pytest.fixture
def con_clave(monkeypatch):
    monkeypatch.setenv("TRAZABILIDAD_HMAC_KEY", CLAVE)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _registrar(session, n: int = 3) -> None:
    repo = SQLAlchemyTrazabilidadRepository(session)
    for i in range(n):
        await RegistrarHashEncadenadoUseCase(repo).execute("EVENTO", {"i": i}, device_id="D-HMAC")
    await session.commit()


@pytest.mark.asyncio
async def test_con_clave_los_eslabones_son_v3_y_verifican(con_clave, db_session):
    await _registrar(db_session)
    registros = await SQLAlchemyTrazabilidadRepository(db_session).listar_todos_ordenados("D-HMAC")

    assert {r.hash_version for r in registros} == {3}
    assert all(r.verificar_integridad(CLAVE.encode()) for r in registros)
    resultado = await VerificarIntegridadRegistroUseCase(SQLAlchemyTrazabilidadRepository(db_session)).execute("D-HMAC")
    assert resultado.integra is True


@pytest.mark.asyncio
async def test_reescritura_con_sha256_sin_clave_es_detectada(con_clave, db_session):
    """Simula a quien tiene escritura en la base pero no la clave: cambia un
    payload y recalcula TODOS los hashes con la fórmula pública v2."""
    from sqlalchemy import select

    from src.infrastructure.database.models import TraceabilityRecordModel

    await _registrar(db_session)
    modelos = (
        await db_session.execute(
            select(TraceabilityRecordModel)
            .where(TraceabilityRecordModel.chain_id == "D-HMAC")
            .order_by(TraceabilityRecordModel.chain_seq)
        )
    ).scalars().all()
    previo = "0" * 64
    for modelo in modelos:
        if modelo.chain_seq == 2:
            modelo.payload = {"i": 999}
        modelo.previous_hash = previo
        modelo.hash_actual = HashEncadenado.calcular_hash(
            modelo.chain_id, previo, timestamp_canonico(modelo.timestamp), modelo.payload
        )
        previo = modelo.hash_actual
    await db_session.commit()

    resultado = await VerificarIntegridadRegistroUseCase(SQLAlchemyTrazabilidadRepository(db_session)).execute("D-HMAC")
    assert resultado.integra is False
    assert resultado.primer_registro_inconsistente == 0


@pytest.mark.asyncio
async def test_eslabon_v3_sin_clave_no_se_da_por_integro(con_clave, db_session, monkeypatch):
    await _registrar(db_session, n=1)
    monkeypatch.setenv("TRAZABILIDAD_HMAC_KEY", "")
    get_settings.cache_clear()

    resultado = await VerificarIntegridadRegistroUseCase(SQLAlchemyTrazabilidadRepository(db_session)).execute("D-HMAC")
    assert resultado.integra is False
    assert "no verificable" in resultado.detalle_inconsistencia.mensaje


def _produccion(**valores) -> Settings:
    base = {
        "environment": "production", "jwt_secret_key": "j" * 40, "allowed_hosts": ["api.example.org"],
        "cors_origins": ["https://app.example.org"], "database_url": "postgresql+asyncpg://u:p@db/prod",
        "mqtt_enabled": False,
    }
    base.update(valores)
    return Settings(**base)


def test_produccion_exige_clave_hmac():
    with pytest.raises(ValueError, match="TRAZABILIDAD_HMAC_KEY"):
        _produccion()
    with pytest.raises(ValueError, match="TRAZABILIDAD_HMAC_KEY"):
        _produccion(trazabilidad_hmac_key="j" * 40)
    assert _produccion(trazabilidad_hmac_key="h" * 40).trazabilidad_hmac_key


def test_hmac_depende_de_la_clave():
    ts = timestamp_canonico(datetime(2026, 1, 1, tzinfo=timezone.utc))
    a = HashEncadenado.calcular_hmac(b"a" * 32, "C", "0" * 64, ts, {"x": 1})
    b = HashEncadenado.calcular_hmac(b"b" * 32, "C", "0" * 64, ts, {"x": 1})
    assert a != b
    assert a != HashEncadenado.calcular_hash("C", "0" * 64, ts, {"x": 1})
