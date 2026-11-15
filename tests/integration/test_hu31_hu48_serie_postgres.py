"""HU-31 / HU-48 sobre PostgreSQL real (tests/postgres.py).

La agregación por intervalo usa SQL distinto por motor: en PostgreSQL la
época es numérica (`EXTRACT(epoch)`) y un CAST redondearía, así que el índice
del intervalo lleva `floor`. SQLite no ejercita ese camino: aquí se comprueba
que una lectura a hh:mm:59.9 cae en su minuto, que las brechas salen de
`lag()` y que las tablas de la migración 0040 admiten el upsert del estado."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.application.use_cases.consultar_serie_termica import ConsultarSerieTermicaUseCase
from src.application.use_cases.registrar_estado_nodo import (
    RegistrarEpisodioBufferUseCase,
    RegistrarEstadoNodoUseCase,
)
from src.infrastructure.database.models import DeviceModel, ThermalReadingModel
from src.infrastructure.database.repositories.estado_nodo_repository import SQLAlchemyEstadoNodoRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from tests.postgres import a_asyncpg, base_migrada

DEV = "PG-SERIE-01"
INICIO = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def dsn():
    with base_migrada() as url:
        yield url


def _correr(dsn: str, corrutina):
    async def _envoltura():
        motor = create_async_engine(a_asyncpg(dsn))
        try:
            return await corrutina(async_sessionmaker(motor, expire_on_commit=False))
        finally:
            await motor.dispose()

    return asyncio.run(_envoltura())


def _fila(segundos: float, **extra) -> dict:
    instante = INICIO + timedelta(seconds=segundos)
    return {
        "device_id": DEV, "timestamp": instante, "received_at": instante, "temperatura_interna": 5.0,
        "estado_conectividad": "online", "time_quality": "synced", "riesgo_efectivo": "normal", **extra,
    }


def test_serie_brechas_y_estado_del_nodo_en_postgresql(dsn):
    async def flujo(fabrica):
        async with fabrica() as session:
            session.add(DeviceModel(id=DEV, created_at=INICIO - timedelta(days=1)))
            await session.flush()
            filas = [_fila(i * 10, boot_id=1, seq_no=i + 1) for i in range(360)]  # 0:00 a 0:59:50
            filas.append(_fila(59.9, boot_id=1, seq_no=1000, temperatura_interna=9.5))  # sigue en el minuto 0
            filas += [_fila(3 * 3600 + i * 10, boot_id=2, seq_no=i + 1) for i in range(6)]  # hueco de 2 h
            filas.append(_fila(3.5 * 3600, boot_id=3, seq_no=1, time_quality="unsynced"))  # sin hora
            await session.execute(insert(ThermalReadingModel), filas)
            await session.commit()

        async with fabrica() as session:
            serie = await ConsultarSerieTermicaUseCase(
                SQLAlchemyLecturaRepository(session), SQLAlchemyEstadoNodoRepository(session)
            ).execute(
                DEV, INICIO, INICIO + timedelta(hours=4), intervalo_reporte_segundos=30, n_intervalos_sin_datos=3,
                bucket_segundos=60, registrado_en=INICIO - timedelta(days=1), ahora=INICIO + timedelta(days=1),
            )
            ultima = await SQLAlchemyLecturaRepository(session).ultima_hora_de_captura(DEV)
        return serie, ultima

    serie, ultima = _correr(dsn, flujo)

    primer_minuto = serie.buckets[0]
    assert primer_minuto.inicio == INICIO
    assert (primer_minuto.lecturas, primer_minuto.temperatura_interna.maximo, primer_minuto.fuera_de_rango) == (
        7, 9.5, 1
    )
    assert len(serie.buckets) == 61
    assert sum(b.lecturas for b in serie.buckets) == serie.resumen.lecturas == 367
    assert serie.resumen.lecturas_sin_hora == 1
    huecos = [(b.desde, b.hasta, b.causa) for b in serie.brechas]
    assert huecos[0] == (INICIO + timedelta(minutes=59, seconds=50), INICIO + timedelta(hours=3), "sin_datos")
    assert huecos[-1][0] == INICIO + timedelta(hours=3, seconds=50)
    assert ultima == INICIO + timedelta(hours=3, seconds=50)


def test_estado_nodo_y_episodios_se_persisten_en_postgresql(dsn):
    recibido = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    detalle = {"perdidas_total": 3, "perdidas_episodio": 3, "episodio_abierto": True, "desde_boot_id": 42,
               "desde_seq_no": 1300, "desde": "2026-11-02T11:00:00Z", "hasta_boot_id": 42, "hasta_seq_no": 1302,
               "hasta": "2026-11-02T11:01:00Z", "causa": "buffer_lleno"}

    async def flujo(fabrica):
        async with fabrica() as session:
            session.add(DeviceModel(id="PG-BUF-01"))
            await session.flush()
            repo = SQLAlchemyEstadoNodoRepository(session)
            await RegistrarEstadoNodoUseCase(repo).execute(
                "PG-BUF-01", {"pendientes": 0, "capacidad": 30000}, recibido_en=recibido
            )
            await RegistrarEstadoNodoUseCase(repo).execute(
                "PG-BUF-01", {"pendientes": 50, "capacidad": 30000, "perdidas_total": 3},
                recibido_en=recibido + timedelta(minutes=5),
            )
            caso = RegistrarEpisodioBufferUseCase(repo)
            await caso.execute("PG-BUF-01", detalle, recibido_en=recibido)
            await caso.execute("PG-BUF-01", {**detalle, "perdidas_episodio": 9, "episodio_abierto": False},
                               recibido_en=recibido + timedelta(minutes=3))
            await session.commit()
        async with fabrica() as session:
            repo = SQLAlchemyEstadoNodoRepository(session)
            return await repo.obtener_estado("PG-BUF-01"), await repo.listar_episodios("PG-BUF-01")

    estado, episodios = _correr(dsn, flujo)

    assert (estado.pendientes, estado.perdidas_total, estado.ultima_sincronizacion_en) == (50, 3, recibido)
    assert estado.detalle == {"pendientes": 50, "capacidad": 30000, "perdidas_total": 3}
    [episodio] = episodios
    assert (episodio.perdidas, episodio.abierto, episodio.desde_seq_no) == (9, False, 1300)
