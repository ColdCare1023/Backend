"""HU-46 esc. 3 (IA-C01, IA-C02): un modelo preventivo rechazado al cargarse
queda como error CRITICAL en el log del proceso, además del MODELO_RECHAZADO
encadenado en la bitácora. Antes era un WARNING (brecha registrada en P-14 b).

Una configuración pendiente, como no tener versión activa (estado intermedio
de la activación o después de POST /desactivar), sigue siendo un aviso."""

import logging
import os

import pytest
from sqlalchemy import select

from src.infrastructure.ai import random_forest_service as rf
from src.infrastructure.ai.prediccion_preventiva import registro
from src.infrastructure.ai.prediccion_preventiva import servicio as modulo_servicio
from src.infrastructure.ai.prediccion_preventiva.servicio import MODO_ACTIVO, ServicioPrediccionPreventiva
from src.infrastructure.ai.random_forest_service import RandomForestRiesgoService
from src.infrastructure.config import get_settings
from src.infrastructure.database.models import AuditLogModel, TraceabilityRecordModel
from src.interface import main
from tests.ia_preventiva import registrar_preventivo

VERSION = "rf-preventivo-e3-1.0.0"

_VARIABLES = ("IA_PREVENTIVO_MODO", "IA_PREVENTIVO_VERSION", "IA_PREVENTIVO_SHA256")


@pytest.fixture
def preventivo(tmp_path, monkeypatch, caplog):
    sha = registrar_preventivo(tmp_path, VERSION)
    anteriores = {k: os.environ.get(k) for k in _VARIABLES}
    os.environ.update(IA_PREVENTIVO_MODO="activo", IA_PREVENTIVO_VERSION=VERSION, IA_PREVENTIVO_SHA256=sha)
    get_settings.cache_clear()
    monkeypatch.setattr(rf, "_instancia", RandomForestRiesgoService())
    # alembic.fileConfig (test_migraciones) deshabilita los loggers existentes.
    for registrador in (main.logger, modulo_servicio.logger):
        monkeypatch.setattr(registrador, "disabled", False)
        monkeypatch.setattr(registrador, "propagate", True)
    caplog.set_level(logging.DEBUG)
    try:
        yield sha, tmp_path
    finally:
        for clave, valor in anteriores.items():
            if valor is None:
                os.environ.pop(clave, None)
            else:
                os.environ[clave] = valor
        get_settings.cache_clear()


def _alterar_un_byte(raiz) -> None:
    ruta = registro.directorio_version(VERSION, raiz) / registro.ARCHIVO_MODELO
    contenido = bytearray(ruta.read_bytes())
    contenido[len(contenido) // 2] ^= 0xFF
    ruta.write_bytes(bytes(contenido))


async def _filas(factory, modelo):
    async with factory() as session:
        return (await session.execute(select(modelo))).scalars().all()


def _criticos(caplog, nombre: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == nombre and r.levelno == logging.CRITICAL]


async def test_artefacto_alterado_al_arrancar_es_critico_y_queda_encadenado(
    preventivo, db_session_factory, monkeypatch, caplog
):
    sha, raiz = preventivo
    _alterar_un_byte(raiz)
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=raiz)
    # La versión V es la activa de la base: lo que falla es el archivo desplegado.
    servicio.fijar_version_activa((VERSION, sha))
    monkeypatch.setattr(modulo_servicio, "_instancia", servicio)
    monkeypatch.setattr(main, "_session_factory", db_session_factory)

    await main._auditar_modelos_rechazados(get_settings())

    (rechazo,) = [f for f in await _filas(db_session_factory, AuditLogModel) if f.accion == "MODELO_RECHAZADO"]
    assert rechazo.detalle["modelo"] == "preventivo"
    assert rechazo.detalle["motivo_codigo"] == "artefacto_no_verificado"
    eslabones = [f for f in await _filas(db_session_factory, TraceabilityRecordModel) if f.tipo_evento == "AUDIT_LOG"]
    assert any(e.payload["accion"] == "MODELO_RECHAZADO" for e in eslabones)
    # Error crítico en el servicio (al cargarlo) y en el arranque (al auditarlo).
    assert _criticos(caplog, modulo_servicio.logger.name)
    (critico,) = _criticos(caplog, main.logger.name)
    assert "artefacto_no_verificado" in critico.getMessage()


async def test_sin_version_activa_al_arrancar_es_un_aviso(preventivo, db_session_factory, monkeypatch, caplog):
    sha, raiz = preventivo
    monkeypatch.setattr(modulo_servicio, "_instancia", ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=raiz))
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    await main._aplicar_version_activa()

    await main._auditar_modelos_rechazados(get_settings())

    (rechazo,) = [f for f in await _filas(db_session_factory, AuditLogModel) if f.accion == "MODELO_RECHAZADO"]
    assert rechazo.detalle["motivo_codigo"] == "sin_version_activa_registrada"
    assert _criticos(caplog, main.logger.name) == []
    assert _criticos(caplog, modulo_servicio.logger.name) == []
    assert any(r.name == main.logger.name and r.levelno == logging.WARNING for r in caplog.records)


async def test_hash_del_entorno_distinto_del_registro_es_critico(preventivo, db_session_factory, monkeypatch, caplog):
    """IA-C02: el SHA-256 del entorno no es el de la versión activa registrada."""
    sha, raiz = preventivo
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=raiz)
    servicio.fijar_version_activa((VERSION, "e" * 64))
    monkeypatch.setattr(modulo_servicio, "_instancia", servicio)
    monkeypatch.setattr(main, "_session_factory", db_session_factory)

    await main._auditar_modelos_rechazados(get_settings())

    (critico,) = _criticos(caplog, main.logger.name)
    assert "hash_distinto_del_registrado" in critico.getMessage()
