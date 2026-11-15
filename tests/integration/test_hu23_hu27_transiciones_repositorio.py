"""HU-23 esc. 1/3 y HU-27 esc. 2: transiciones atómicas del repositorio.

- `marcar_atendida_si_no_atendida` solo pasa de RECONOCIDA a ATENDIDA: una
  alerta PENDIENTE ya no se cierra sin que conste quién la reconoció.
- `reconocer_si_pendiente` solo pasa de PENDIENTE a RECONOCIDA y deja quién y
  cuándo.
- En PostgreSQL, dos acciones correctivas simultáneas sobre una alerta
  PENDIENTE: una la reconoce y la atiende; la otra pierde (409) sin dejar
  reconocimiento ni acción."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.application.use_cases.registrar_accion_correctiva import RegistrarAccionCorrectivaUseCase
from src.application.use_cases.registrar_lectura_termica import RegistrarLecturaTermicaUseCase
from src.domain.entities.alerta_termica import AlertaTermica
from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.entities.usuario import Usuario
from src.domain.exceptions import DomainError
from src.domain.value_objects.estado_alerta import EstadoAlerta
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.domain.value_objects.rol import Rol
from src.infrastructure.ai.random_forest_service import get_random_forest_service
from src.infrastructure.database.models import CorrectiveActionModel, TraceabilityRecordModel
from src.infrastructure.database.repositories.accion_correctiva_repository import (
    SQLAlchemyAccionCorrectivaRepository,
)
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.trazabilidad_repository import SQLAlchemyTrazabilidadRepository
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from src.infrastructure.security.password_hasher import hash_password
from tests.postgres import a_asyncpg, base_migrada

DEVICE_ID = "FARM-HU23-REPO"
AHORA = datetime.now(tz=timezone.utc)


async def _alerta_en(session, estado: EstadoAlerta) -> AlertaTermica:
    lectura = await SQLAlchemyLecturaRepository(session).agregar(
        LecturaTermica(
            device_id=DEVICE_ID, timestamp=AHORA - timedelta(minutes=1), temperatura_ambiental=21.0,
            humedad_ambiental=55.0, temperatura_interna=9.5, apertura_refrigerador=False,
            estado_conectividad="online",
        )
    )
    return await SQLAlchemyAlertaRepository(session).agregar(
        AlertaTermica(
            reading_id=lectura.id, device_id=DEVICE_ID, nivel_riesgo=NivelRiesgo.EXCURSION_CRITICA,
            mensaje="m", estado=estado,
        )
    )


@pytest.mark.parametrize(
    ("estado", "transiciona"),
    [(EstadoAlerta.PENDIENTE, False), (EstadoAlerta.RECONOCIDA, True), (EstadoAlerta.ATENDIDA, False)],
)
async def test_atender_solo_desde_reconocida(db_session, estado, transiciona):
    alerta = await _alerta_en(db_session, estado)
    repositorio = SQLAlchemyAlertaRepository(db_session)

    assert await repositorio.marcar_atendida_si_no_atendida(alerta.id, AHORA) is transiciona

    releida = await repositorio.obtener_por_id(alerta.id)
    assert releida.estado == (EstadoAlerta.ATENDIDA if transiciona else estado)


async def test_reconocer_si_pendiente_deja_quien_y_cuando(db_session, crear_usuario):
    qf = await crear_usuario("QF Repo", "qf.repo@farmacia.example.org", "password123", Rol.FARMACEUTICO)
    alerta = await _alerta_en(db_session, EstadoAlerta.PENDIENTE)
    repositorio = SQLAlchemyAlertaRepository(db_session)

    assert await repositorio.reconocer_si_pendiente(alerta.id, qf.id, AHORA) is True
    assert await repositorio.reconocer_si_pendiente(alerta.id, uuid4(), AHORA) is False  # ya no está PENDIENTE

    releida = await repositorio.obtener_por_id(alerta.id)
    assert releida.estado == EstadoAlerta.RECONOCIDA
    assert (releida.revisada, releida.revisada_por) == (True, qf.id)
    assert releida.reconocida_en is not None


# ── PostgreSQL: dos QF registran la acción sobre la misma PENDIENTE ───────


@pytest.fixture(scope="module")
def dsn():
    with base_migrada() as url:
        yield url


async def _usuario(fabrica, email: str) -> Usuario:
    async with fabrica() as session:
        usuario = await SQLAlchemyUsuarioRepository(session).agregar(
            Usuario(nombre=email, email=email, password_hash=hash_password("password123"), rol=Rol.FARMACEUTICO)
        )
        await session.commit()
        return usuario


async def _alerta_pendiente(fabrica) -> AlertaTermica:
    async with fabrica() as session:
        await RegistrarLecturaTermicaUseCase(
            SQLAlchemyLecturaRepository(session), SQLAlchemyAlertaRepository(session),
            SQLAlchemyTrazabilidadRepository(session), ClasificarRiesgoTermicoUseCase(get_random_forest_service()),
            device_repository=SQLAlchemyDeviceRepository(session),
        ).execute(
            LecturaTermica(
                device_id=DEVICE_ID, timestamp=AHORA - timedelta(minutes=1), temperatura_ambiental=21.0,
                humedad_ambiental=55.0, temperatura_interna=10.2, apertura_refrigerador=False,
                estado_conectividad="online",
            )
        )
        await session.commit()
        return await SQLAlchemyAlertaRepository(session).obtener_episodio_abierto(DEVICE_ID)


async def _registrar(fabrica, alerta_id, usuario_id) -> str:
    async with fabrica() as session:
        try:
            await RegistrarAccionCorrectivaUseCase(
                SQLAlchemyAccionCorrectivaRepository(session), SQLAlchemyAlertaRepository(session),
                SQLAlchemyTrazabilidadRepository(session), SQLAlchemyAuditLogRepository(session),
            ).execute(alerta_id, usuario_id, f"Acción de {usuario_id}", "Trasladar el lote", "Fuera de rango")
        except DomainError:
            await session.rollback()
            return "409"
        await session.commit()
        return "201"


def test_postgres_acciones_simultaneas_sobre_una_pendiente(dsn):
    async def flujo():
        motor = create_async_engine(a_asyncpg(dsn), pool_size=6)
        fabrica = async_sessionmaker(motor, expire_on_commit=False)
        try:
            qf_a, qf_b = await _usuario(fabrica, "qf.a@farmacia.example.org"), await _usuario(
                fabrica, "qf.b@farmacia.example.org"
            )
            alerta = await _alerta_pendiente(fabrica)
            resultados = await asyncio.gather(
                _registrar(fabrica, alerta.id, qf_a.id), _registrar(fabrica, alerta.id, qf_b.id)
            )
            async with fabrica() as session:
                final = await SQLAlchemyAlertaRepository(session).obtener_por_id(alerta.id)
                acciones = (await session.execute(select(CorrectiveActionModel))).scalars().all()
                revisiones = [
                    r for r in (await session.execute(select(TraceabilityRecordModel))).scalars().all()
                    if r.tipo_evento == "AUDIT_LOG" and r.payload.get("accion") == "REVISAR_ALERTA"
                ]
            return resultados, final, acciones, revisiones, {qf_a.id, qf_b.id}
        finally:
            await motor.dispose()

    resultados, final, acciones, revisiones, qfs = asyncio.run(flujo())

    assert sorted(resultados) == ["201", "409"]
    assert final.estado == EstadoAlerta.ATENDIDA
    assert len(acciones) == 1
    # Quien reconoció es quien registró la única acción, y quedó un solo eslabón.
    assert final.revisada_por == acciones[0].usuario_id and final.revisada_por in qfs
    assert len(revisiones) == 1
    assert revisiones[0].payload["detalle"]["origen"] == "accion_correctiva"
