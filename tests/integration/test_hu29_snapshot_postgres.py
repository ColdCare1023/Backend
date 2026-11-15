"""HU-29 esc. 1-3 (P0-4 de la validación final, P1-6 y P1-7 de la
revalidación) con PostgreSQL, pg_dump y pg_restore reales.

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
import logging
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


# ── HU-29 esc. 3: el cron tal como corre en Railway (`main`) ────────────────


@pytest.fixture
def cron_pg(cliente_pg, monkeypatch):
    """`respaldo_postgres.main` lee DATABASE_URL de Settings, igual que el
    servicio cron: se apunta a la base migrada de la prueba."""
    from src.infrastructure.config import get_settings
    from src.interface import tareas

    url = a_asyncpg(cliente_pg)
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    # alembic.fileConfig (base_migrada) deshabilita los loggers existentes.
    monkeypatch.setattr(respaldo_postgres.logger, "disabled", False)
    monkeypatch.setattr(tareas.logger, "disabled", False)
    yield url
    get_settings.cache_clear()


async def _filas_respaldo_fallido(url: str) -> dict:
    motor = create_async_engine(url)
    try:
        async with async_sessionmaker(motor)() as session:
            filas = (
                await session.execute(select(AuditLogModel).where(AuditLogModel.accion == "RESPALDO_FALLIDO"))
            ).scalars().all()
            return {fila.id: fila for fila in filas}
    finally:
        await motor.dispose()


async def _vigilar(url: str) -> str | None:
    """Una pasada del vigilante de la API (servicio Backend) sobre la misma base."""
    from src.infrastructure.database.session import build_engine, build_session_factory
    from src.interface.tareas import VigilanteRespaldos

    motor = build_engine(url)
    try:
        return await VigilanteRespaldos(build_session_factory(motor), 3600, 26).ejecutar_una_vez()
    finally:
        await motor.dispose()


def _criticos(caplog, logger: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL and r.name == logger]


@pytest.mark.parametrize("causa", ["almacenamiento", "pg_major_anterior"])
def test_un_fallo_del_cron_llega_al_log_del_backend(cron_pg, tmp_path, caplog, monkeypatch, causa):
    """HU-29 esc. 3 de punta a punta, con la base accesible: el cron falla, deja
    RESPALDO_FALLIDO en audit_logs y sale con 2; el vigilante de la API lo
    convierte en una alerta CRÍTICA en el log del backend.

    - `almacenamiento`: el destino cuelga de un archivo regular, no de una
      carpeta (el ejemplo del criterio: «límites de almacenamiento»).
    - `pg_major_anterior`: el fallo provocado de RESPALDO_RAILWAY.md, un cron
      construido con un PG_MAJOR anterior al servidor."""
    from src.interface import tareas

    url = cron_pg
    caplog.set_level(logging.INFO)

    # Antes del fallo hay un respaldo correcto: el vigilante está al día.
    assert respaldo_postgres.main(["--destino", str(tmp_path / "respaldos")]) == 0
    assert asyncio.run(_vigilar(url)) is None
    previas = asyncio.run(_filas_respaldo_fallido(url))

    if causa == "almacenamiento":
        bloqueo = tmp_path / "volumen_lleno"
        bloqueo.write_text("no es una carpeta", encoding="utf-8")
        destino = bloqueo / "sub"
    else:
        monkeypatch.setattr(respaldo_postgres, "version_pg_dump", lambda: "pg_dump (PostgreSQL) 9.6.24")
        destino = tmp_path / "respaldos"
    assert respaldo_postgres.main(["--destino", str(destino)]) == respaldo_postgres.CODIGO_FALLO

    # Log del cron (Railway marca la ejecución como fallida por el código 2).
    assert any(m.startswith("RESPALDO FALLIDO: ") for m in _criticos(caplog, respaldo_postgres.logger.name))
    # Fila de auditoría con el resultado y el motivo.
    nuevas = asyncio.run(_filas_respaldo_fallido(url))
    [fila] = [fila for identificador, fila in nuevas.items() if identificador not in previas]
    assert fila.resultado == "fallido"
    assert fila.recurso == "respaldo/postgres" and fila.usuario_id is None
    assert fila.detalle["error"]
    if causa == "pg_major_anterior":
        assert "pg_dump 9 es anterior al servidor PostgreSQL" in fila.detalle["error"]
        assert "PG_MAJOR=" in fila.detalle["error"]

    # Log del backend: la pasada del vigilante deja la alerta operativa crítica.
    assert asyncio.run(_vigilar(url)) == "fallido"
    [alerta] = _criticos(caplog, tareas.logger.name)
    assert alerta.startswith("ALERTA OPERATIVA: el último respaldo programado de PostgreSQL falló")


def test_un_volcado_parcial_se_descarta_y_la_retencion_no_lo_cuenta(cron_pg, tmp_path, monkeypatch):
    """P1-7: un pg_dump cortado (p. ej. por falta de espacio en el volumen)
    dejaba `coldcare_<UTC>.dump` a medias. `podar_respaldos` lo contaba como
    uno de los `retener` más recientes y, tras el siguiente respaldo correcto,
    borraba a cambio un respaldo bueno."""
    destino = tmp_path / "respaldos"
    destino.mkdir()
    buenos = ["coldcare_20261110T070000Z", "coldcare_20261111T070000Z"]
    for base in buenos:
        (destino / f"{base}.dump").write_bytes(b"PGDMP")
        (destino / f"{base}.manifiesto.json").write_text("{}", encoding="utf-8")

    def _volcado_cortado(database_url, archivo, snapshot=None):
        archivo.write_bytes(b"PGDMP" + bytes(64))  # pg_dump alcanzó a escribir parte del archivo
        raise subprocess.CalledProcessError(
            1, ["pg_dump"], stderr="pg_dump: error: could not write to output file: No space left on device"
        )

    monkeypatch.setattr(respaldo_postgres, "ejecutar_pg_dump", _volcado_cortado)

    assert respaldo_postgres.main(["--destino", str(destino), "--retener", "2"]) == respaldo_postgres.CODIGO_FALLO

    assert sorted(p.name for p in destino.iterdir()) == sorted(
        f"{base}{sufijo}" for base in buenos for sufijo in (".dump", ".manifiesto.json")
    )
    # La poda que sigue a un respaldo correcto ya no cuenta el parcial.
    assert respaldo_postgres.podar_respaldos(destino, retener=2) == []
