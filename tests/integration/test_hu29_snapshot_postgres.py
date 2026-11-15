"""HU-29 esc. 1-2 (P0-4 de la validación final) con PostgreSQL, pg_dump y
pg_restore reales.

Antes, la huella se calculaba DESPUÉS del pg_dump y fuera de su snapshot: con
el nodo publicando, una lectura que entraba entre ambos quedaba en la huella
pero no en el `.dump`, y la prueba de restauración daba `coincide=false`
aunque el respaldo fuera correcto. Ahora volcado y huella salen del mismo
snapshot exportado (`pg_dump --snapshot`).

Usa el PostgreSQL embebido (pgserver), que trae pg_dump y pg_restore de su
misma versión; sin él, el de TEST_POSTGRES_URL con los del sistema si no son
más antiguos que el servidor. Si no hay ninguno, la prueba se omite con motivo.
"""

import asyncio
import itertools
import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scripts import respaldo_postgres, verificar_restauracion
from src.infrastructure.database.models import (
    AuditLogModel,
    DeviceConfigHistoryModel,
    DeviceModel,
    RoleModel,
    ThermalReadingModel,
    UserModel,
)
from src.infrastructure.respaldo import huellas
from tests.postgres import _con_base, a_asyncpg, base_migrada, binarios_pgserver

DEVICE = "FARM-HU29-PG"
_TANDA = itertools.count()


@pytest.fixture(scope="module")
def dsn():
    with base_migrada(preferir_embebido=True) as url:
        yield url


@pytest.fixture
def cliente_pg(dsn, monkeypatch):
    """pg_dump/pg_restore compatibles con el servidor de la prueba."""
    carpeta = binarios_pgserver()  # el servidor de `dsn` es pgserver si está instalado
    if carpeta is not None:
        monkeypatch.setenv("PATH", f"{carpeta}{os.pathsep}{os.environ.get('PATH', '')}")
    if shutil.which("pg_dump") is None or shutil.which("pg_restore") is None:
        pytest.skip("Sin pg_dump/pg_restore en el PATH")
    cliente = int(re.search(r"(\d+)", subprocess.run(
        ["pg_dump", "--version"], check=True, capture_output=True, text=True
    ).stdout).group(1))
    servidor = asyncio.run(_version_servidor(dsn)) // 10000
    if cliente < servidor:
        pytest.skip(f"pg_dump {cliente} es anterior al servidor PostgreSQL {servidor}")
    return dsn


async def _version_servidor(dsn: str) -> int:
    import asyncpg

    conexion = await asyncpg.connect(dsn)
    try:
        return int(await conexion.fetchval("SHOW server_version_num"))
    finally:
        await conexion.close()


async def _crear_base(dsn: str) -> str:
    import asyncpg

    nombre = f"restaurada_{uuid.uuid4().hex[:10]}"
    conexion = await asyncpg.connect(dsn)
    try:
        await conexion.execute(f'CREATE DATABASE "{nombre}"')
    finally:
        await conexion.close()
    return _con_base(dsn, nombre)


async def _insertar(dsn: str, etiqueta: str, lecturas: int) -> None:
    """Datos en varias tablas de evidencia (lecturas, bitácora, usuarios,
    equipos e historial de configuración), confirmados."""
    motor = create_async_engine(a_asyncpg(dsn))
    try:
        async with async_sessionmaker(motor, expire_on_commit=False)() as session:
            if await session.get(DeviceModel, DEVICE) is None:
                session.add(DeviceModel(id=DEVICE, nombre="Refrigerador HU-29"))
                await session.flush()
            rol = (await session.execute(select(RoleModel).limit(1))).scalar_one()
            session.add(UserModel(
                nombre=f"Usuario {etiqueta}", email=f"{etiqueta}-{uuid.uuid4().hex[:6]}@coldcare.test",
                password_hash="$argon2id$prueba", rol_id=rol.id,
            ))
            # (device_id, timestamp) es único: cada tanda en su propia hora.
            base = datetime(2026, 11, 1, tzinfo=timezone.utc) + timedelta(hours=next(_TANDA))
            for i in range(lecturas):
                session.add(ThermalReadingModel(
                    device_id=DEVICE, timestamp=base + timedelta(seconds=30 * i), temperatura_interna=5.0 + i / 100,
                    origen="dispositivo",
                ))
            session.add(AuditLogModel(accion=f"PRUEBA_{etiqueta.upper()}", recurso="respaldo", detalle={"n": lecturas}))
            session.add(DeviceConfigHistoryModel(device_id=DEVICE, campo="ubicacion", valor_nuevo=etiqueta))
            await session.commit()
    finally:
        await motor.dispose()


def test_filas_que_entran_tras_el_volcado_no_rompen_la_restauracion(cliente_pg, tmp_path, monkeypatch):
    dsn = cliente_pg
    url = a_asyncpg(dsn)
    asyncio.run(_insertar(dsn, "antes", lecturas=7))
    # Páginas pequeñas: la huella recorre varias páginas por clave.
    monkeypatch.setattr(huellas, "LOTE", 3)

    volcar = respaldo_postgres.ejecutar_pg_dump

    def _volcar_y_seguir_publicando(*args, **kwargs):
        volcar(*args, **kwargs)
        # El nodo sigue publicando entre el volcado y el cálculo de la huella
        # (otra conexión, en otro hilo, con su propio bucle de eventos).
        hilo = threading.Thread(target=lambda: asyncio.run(_insertar(dsn, "durante", lecturas=4)))
        hilo.start()
        hilo.join()

    monkeypatch.setattr(respaldo_postgres, "ejecutar_pg_dump", _volcar_y_seguir_publicando)

    manifiesto = asyncio.run(respaldo_postgres.respaldar(url, tmp_path))
    destino = asyncio.run(_crear_base(dsn))
    ruta_manifiesto = next(tmp_path.glob("coldcare_*.manifiesto.json"))
    informe = verificar_restauracion.verificar(ruta_manifiesto, a_asyncpg(destino), url)

    assert informe["archivo_integro"] is True
    assert informe["coincide"] is True, json.dumps(informe["tablas"], indent=2)
    # La base viva sí tiene las filas nuevas: el respaldo es el del snapshot.
    vivas = asyncio.run(respaldo_postgres._huellas(url))
    assert vivas["thermal_readings"]["filas"] == manifiesto["huellas"]["thermal_readings"]["filas"] + 4
    assert manifiesto["consistencia"] == "snapshot_exportado_repeatable_read"
    # HU-29 esc. 2: usuarios y metadata crítica también se verifican al 100 %.
    assert set(manifiesto["huellas"]) == set(huellas.TABLAS_EVIDENCIA)
    assert vivas["users"]["filas"] == manifiesto["huellas"]["users"]["filas"] + 1
    for tabla in ("users", "devices", "thermal_alerts", "corrective_actions", "device_config_history"):
        assert informe["tablas"][tabla]["coincide_huella"] is True


def test_la_huella_por_clave_es_la_misma_con_cualquier_tamano_de_pagina(cliente_pg, monkeypatch):
    url = a_asyncpg(cliente_pg)
    asyncio.run(_insertar(cliente_pg, "paginas", lecturas=10))
    completa = asyncio.run(respaldo_postgres._huellas(url))
    monkeypatch.setattr(huellas, "LOTE", 2)
    paginada = asyncio.run(respaldo_postgres._huellas(url))
    assert paginada == completa


def test_un_pg_dump_mas_antiguo_que_el_servidor_falla_con_motivo_claro(monkeypatch):
    monkeypatch.setattr(respaldo_postgres, "version_pg_dump", lambda: "pg_dump (PostgreSQL) 15.4")
    with pytest.raises(RuntimeError, match="PG_MAJOR=17"):
        respaldo_postgres.verificar_cliente_compatible(170002)
    respaldo_postgres.verificar_cliente_compatible(150010)
