"""HU-23 esc. 1/3 y HU-27 esc. 2: transiciones atómicas del repositorio.

- `marcar_atendida_si_no_atendida` solo pasa de RECONOCIDA a ATENDIDA: una
  alerta PENDIENTE ya no se cierra sin que conste quién la reconoció.
- `reconocer_si_pendiente` solo pasa de PENDIENTE a RECONOCIDA y deja quién y
  cuándo.
- En PostgreSQL, dos acciones correctivas simultáneas sobre una alerta
  PENDIENTE: una la reconoce y la atiende; la otra pierde (409) sin dejar
  reconocimiento ni acción.
- HU-23 esc. 3: un PATCH /revisar que se cruza con una acción correctiva en
  curso sobre la misma PENDIENTE responde 409 y la alerta queda ATENDIDA (antes
  el leer-modificar-escribir la devolvía a RECONOCIDA).
- HU-25/HU-27: la acción toma el candado de la cadena del equipo antes de
  actualizar la alerta (orden de la ingesta), así que una lectura concurrente
  del mismo equipo ya no produce un deadlock que aborte la acción."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.application.use_cases.consultar_alertas import MarcarAlertaRevisadaUseCase
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
from src.infrastructure.database.models import (
    CorrectiveActionModel,
    ThermalAlertModel,
    TraceabilityRecordModel,
)
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


async def _alerta_en(session, estado: EstadoAlerta, device_id: str = DEVICE_ID) -> AlertaTermica:
    lectura = await SQLAlchemyLecturaRepository(session).agregar(
        LecturaTermica(
            device_id=device_id, timestamp=AHORA - timedelta(minutes=1), temperatura_ambiental=21.0,
            humedad_ambiental=55.0, temperatura_interna=9.5, apertura_refrigerador=False,
            estado_conectividad="online",
        )
    )
    return await SQLAlchemyAlertaRepository(session).agregar(
        AlertaTermica(
            reading_id=lectura.id, device_id=device_id, nivel_riesgo=NivelRiesgo.EXCURSION_CRITICA,
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


class _LecturaDesfasada(SQLAlchemyAlertaRepository):
    """La primera lectura devuelve la alerta como PENDIENTE aunque en la base
    ya esté ATENDIDA: es lo que ve, por MVCC, un PATCH /revisar que leyó antes
    del COMMIT de una acción correctiva concurrente."""

    def __init__(self, session) -> None:
        super().__init__(session)
        self._primera = True

    async def obtener_por_id(self, alerta_id, refrescar: bool = False):
        alerta = await super().obtener_por_id(alerta_id, refrescar=refrescar)
        if self._primera and alerta is not None:
            self._primera = False
            alerta.estado = EstadoAlerta.PENDIENTE
            alerta.atendida_en = None
        return alerta


async def test_reconocer_con_lectura_desfasada_no_devuelve_una_atendida_a_reconocida(db_session, crear_usuario):
    qf = await crear_usuario("QF Desfase", "qf.desfase@farmacia.example.org", "password123", Rol.FARMACEUTICO)
    alerta = await _alerta_en(db_session, EstadoAlerta.ATENDIDA)

    with pytest.raises(DomainError, match="ya fue reconocida o atendida"):
        await MarcarAlertaRevisadaUseCase(_LecturaDesfasada(db_session)).execute(alerta.id, qf.id)

    releida = await SQLAlchemyAlertaRepository(db_session).obtener_por_id(alerta.id, refrescar=True)
    assert releida.estado == EstadoAlerta.ATENDIDA
    assert releida.revisada_por is None


async def test_reconocer_devuelve_la_alerta_releida_de_la_base(db_session, crear_usuario):
    qf = await crear_usuario("QF Relee", "qf.relee@farmacia.example.org", "password123", Rol.FARMACEUTICO)
    alerta = await _alerta_en(db_session, EstadoAlerta.PENDIENTE)

    reconocida = await MarcarAlertaRevisadaUseCase(
        SQLAlchemyAlertaRepository(db_session), SQLAlchemyTrazabilidadRepository(db_session)
    ).execute(alerta.id, qf.id)

    assert reconocida.estado == EstadoAlerta.RECONOCIDA
    assert (reconocida.revisada, reconocida.revisada_por) == (True, qf.id)
    assert reconocida.reconocida_en is not None


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


# ── PostgreSQL: PATCH /revisar cruzado con una acción en curso (HU-23 esc. 3) ──


def test_postgres_revisar_cruzado_con_una_accion_en_curso_es_409_y_queda_atendida(dsn):
    async def flujo():
        motor = create_async_engine(a_asyncpg(dsn), pool_size=4)
        fabrica = async_sessionmaker(motor, expire_on_commit=False)
        try:
            qf_a = await _usuario(fabrica, "qf.revisa@farmacia.example.org")
            qf_b = await _usuario(fabrica, "qf.actua@farmacia.example.org")
            async with fabrica() as session:
                alerta = await _alerta_en(session, EstadoAlerta.PENDIENTE, "FARM-HU23-REVISAR")
                await session.commit()

            # B: acción correctiva en curso (reconoce y atiende) sin COMMIT todavía.
            sesion_b = fabrica()
            await RegistrarAccionCorrectivaUseCase(
                SQLAlchemyAccionCorrectivaRepository(sesion_b), SQLAlchemyAlertaRepository(sesion_b),
                SQLAlchemyTrazabilidadRepository(sesion_b), SQLAlchemyAuditLogRepository(sesion_b),
            ).execute(alerta.id, qf_b.id, "Acción", "Trasladar", "Fuera de rango")

            # A: PATCH /revisar llega mientras B no confirmó (por MVCC lee PENDIENTE).
            sesion_a = fabrica()

            async def revisar() -> str:
                try:
                    await MarcarAlertaRevisadaUseCase(
                        SQLAlchemyAlertaRepository(sesion_a), SQLAlchemyTrazabilidadRepository(sesion_a)
                    ).execute(alerta.id, qf_a.id)
                    await sesion_a.commit()
                    return "200"
                except DomainError:
                    await sesion_a.rollback()
                    return "409"

            tarea = asyncio.create_task(revisar())
            await asyncio.sleep(0.5)  # A espera el candado o la fila que retiene B
            await sesion_b.commit()
            await sesion_b.close()
            resultado = await tarea
            await sesion_a.close()
            async with fabrica() as session:
                final = await SQLAlchemyAlertaRepository(session).obtener_por_id(alerta.id)
            return resultado, final, qf_b.id
        finally:
            await motor.dispose()

    resultado, final, qf_b = asyncio.run(flujo())

    assert resultado == "409"
    assert final.estado == EstadoAlerta.ATENDIDA
    assert final.atendida_en is not None
    assert final.revisada_por == qf_b


# ── PostgreSQL: orden de bloqueos ingesta vs. acción (HU-25/HU-27) ────────

EQUIPO_CANDADO = "FARM-HU27-CANDADO"


def test_postgres_accion_concurrente_con_la_ingesta_del_mismo_equipo_no_se_aborta(dsn):
    """La ingesta toma el candado de la cadena del equipo (eslabón de la
    lectura) y luego actualiza el episodio abierto. Si la acción actualizara
    primero la alerta y después pidiera el candado, PostgreSQL detectaría un
    deadlock y abortaría una de las dos."""

    async def flujo():
        motor = create_async_engine(a_asyncpg(dsn), pool_size=4)
        fabrica = async_sessionmaker(motor, expire_on_commit=False)
        try:
            qf = await _usuario(fabrica, "qf.candado@farmacia.example.org")
            async with fabrica() as session:
                alerta = await _alerta_en(session, EstadoAlerta.PENDIENTE, EQUIPO_CANDADO)
                await session.commit()

            ingesta = fabrica()
            await ingesta.execute(text("SELECT pg_advisory_xact_lock(hashtext(:c))"), {"c": EQUIPO_CANDADO})

            async def accion() -> str:
                async with fabrica() as session:
                    try:
                        await RegistrarAccionCorrectivaUseCase(
                            SQLAlchemyAccionCorrectivaRepository(session), SQLAlchemyAlertaRepository(session),
                            SQLAlchemyTrazabilidadRepository(session), SQLAlchemyAuditLogRepository(session),
                        ).execute(alerta.id, qf.id, "Acción", "Trasladar", "Fuera de rango")
                        await session.commit()
                        return "ok"
                    except Exception as exc:
                        return f"abortada: {type(exc).__name__}"

            tarea = asyncio.create_task(accion())
            await asyncio.sleep(0.5)
            try:
                # La ingesta sigue: actualiza el episodio abierto del equipo.
                await ingesta.execute(
                    update(ThermalAlertModel)
                    .where(ThermalAlertModel.id == alerta.id)
                    .values(ultima_actualizacion=datetime.now(tz=timezone.utc))
                )
                await ingesta.commit()
                r_ingesta = "ok"
            except Exception as exc:
                await ingesta.rollback()
                r_ingesta = f"abortada: {type(exc).__name__}"
            r_accion = await tarea
            await ingesta.close()
            async with fabrica() as session:
                final = await SQLAlchemyAlertaRepository(session).obtener_por_id(alerta.id)
            return r_ingesta, r_accion, final
        finally:
            await motor.dispose()

    r_ingesta, r_accion, final = asyncio.run(flujo())

    assert (r_ingesta, r_accion) == ("ok", "ok")
    assert final.estado == EstadoAlerta.ATENDIDA
