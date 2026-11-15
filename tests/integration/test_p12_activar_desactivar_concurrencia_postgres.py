"""P-12 / HU-47 esc. 2: /activar y /desactivar simultáneos sobre PostgreSQL
real (tests/postgres.py) no dejan la historia distinta de la base.

Sin un candado común, cada caso de uso lee qué versión está activa y toma su
hora (`cuando`) sin esperar al otro. Dos intercalados lo rompían:

- misma versión: desactivar(X) toma su hora, activar(X) termina en medio (no
  emite UPDATE porque X «ya está activa») y luego desactivar confirma. X queda
  inactiva, pero el último evento por hora es la ACTIVACIÓN y
  `obtener_version_activa_en(ahora)` responde X;
- otra versión: activar(Y) toma su hora, desactivar(X) confirma en medio y
  luego activar(Y) confirma. Y queda activa, pero el último evento por hora es
  la DESACTIVACIÓN y HU-47 diría que las inferencias de Y no tenían versión.

Las pruebas fuerzan esos intercalados (la primera operación se detiene justo
después de tomar su hora, hasta que la segunda termina o pasan ESPERA_S) y
también los corren libres varias veces. Después de cada par se exige que las
filas, `obtener_version_activa_en(ahora)` y el último evento de la cadena
(por `chain_seq`) digan lo mismo, que las horas no retrocedan y que el par
termine 200/200 o 200/409. SQLite en memoria serializa todo y no reproduce
la carrera."""

import asyncio
from datetime import datetime, timezone
from functools import partial

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.application.use_cases.gestionar_modelo_ia import (
    EVENTO_VERSION_ACTIVADA,
    EVENTO_VERSION_DESACTIVADA,
    ActivarVersionModeloUseCase,
    DesactivarVersionModeloUseCase,
    VersionNoActivaError,
)
from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.application.use_cases.verificar_integridad_registro import VerificarIntegridadRegistroUseCase
from src.domain.entities.usuario import Usuario
from src.domain.entities.version_modelo_ia import VersionModeloIA
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.models import ModeloIAVersionModel, TraceabilityRecordModel
from src.infrastructure.database.repositories.modelo_ia_repository import SQLAlchemyModeloIARepository
from src.infrastructure.database.repositories.trazabilidad_repository import SQLAlchemyTrazabilidadRepository
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from tests.postgres import a_asyncpg, base_migrada

X = "rf-p12-carrera-x-1.0.0"
Y = "rf-p12-carrera-y-1.0.0"
EMAIL_ADMIN = "admin.carrera@farmacia.example.org"
# Cuánto espera la operación detenida a que la otra termine. Sin candado la
# otra termina en milisegundos; con candado la otra espera y la pausa vence.
ESPERA_S = 0.5
REPETICIONES_FORZADAS = 2
REPETICIONES_LIBRES = 5
# Cumplen el criterio por incidente (gestionar_modelo_ia.UMBRALES_POR_INCIDENTE).
METRICAS = {
    "f1_macro": 0.88, "incidentes_evaluables": 12, "sensibilidad_incidente": 0.92,
    "ic95_inferior_sensibilidad": 0.62, "falsas_alarmas_por_dia_sensor": 1.1,
    "mediana_anticipacion_min": 9.0, "ap_random_forest": 0.71, "ap_mejor_baseline": 0.55,
}
PARTICIONES = {"entrenamiento": "C1-C4", "prueba": "C5", "separacion_min": 30}
MOTIVO = "Vuelta atrás concurrente de prueba"


@pytest.fixture(scope="module")
def entorno():
    """DSN de una base migrada con X activa e Y registrada, y el id del admin."""
    with base_migrada() as url:
        yield url, asyncio.run(_preparar(url))


async def _preparar(dsn):
    motor = create_async_engine(a_asyncpg(dsn))
    fabrica = async_sessionmaker(motor, expire_on_commit=False)
    try:
        async with fabrica() as session:
            admin = await SQLAlchemyUsuarioRepository(session).agregar(Usuario(
                nombre="Admin carrera", email=EMAIL_ADMIN, password_hash="x" * 60, rol=Rol.ADMINISTRADOR,
            ))
            repositorio = SQLAlchemyModeloIARepository(session)
            for version, sha in ((X, "a" * 64), (Y, "b" * 64)):
                await repositorio.registrar(VersionModeloIA(
                    version=version, model_hash=sha, feature_schema_version="termico_minimo",
                    dataset_version="corridas-2026-11", scikit_learn_version="1.9.0", python_version="3.12.13",
                    trained_at=datetime(2026, 11, 1, tzinfo=timezone.utc), aprobado_por=admin.id,
                    horizonte_min=30, metricas=METRICAS, particiones=PARTICIONES,
                ))
            await session.commit()
        await _activar(fabrica, admin.id, X, SQLAlchemyModeloIARepository)
        return admin.id
    finally:
        await motor.dispose()


class _RepositorioQueSeDetiene(SQLAlchemyModeloIARepository):
    """Se detiene al entrar en activar/desactivar, es decir, después de que el
    caso de uso leyó la versión activa y tomó su hora, hasta que la otra
    operación termina o pasa ESPERA_S."""

    def __init__(self, session, detenida: asyncio.Event, otra_termino: asyncio.Event) -> None:
        super().__init__(session)
        self._detenida = detenida
        self._otra_termino = otra_termino

    async def _detenerse(self) -> None:
        self._detenida.set()
        try:
            await asyncio.wait_for(self._otra_termino.wait(), ESPERA_S)
        except TimeoutError:
            pass

    async def activar(self, version, cuando):
        await self._detenerse()
        return await super().activar(version, cuando)

    async def desactivar(self, version, cuando):
        await self._detenerse()
        return await super().desactivar(version, cuando)


async def _activar(fabrica, admin_id, version, repositorio) -> str:
    async with fabrica() as session:
        await ActivarVersionModeloUseCase(
            repositorio(session), RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session)),
        ).execute(version, admin_id)
        await session.commit()
        return "200"


async def _desactivar(fabrica, admin_id, version, repositorio) -> str:
    async with fabrica() as session:
        try:
            await DesactivarVersionModeloUseCase(
                repositorio(session), RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session)),
            ).execute(version, admin_id, MOTIVO)
        except VersionNoActivaError:
            await session.rollback()
            return "409"
        await session.commit()
        return "200"


async def _dejar_x_activa(fabrica, admin_id) -> None:
    async with fabrica() as session:
        activa = await SQLAlchemyModeloIARepository(session).obtener_activa()
    if activa is None or activa.version != X:
        await _activar(fabrica, admin_id, X, SQLAlchemyModeloIARepository)


async def _estado(fabrica) -> dict:
    async with fabrica() as session:
        filas = {
            f.version: {"activa": f.activa, "activada_en": f.activada_en, "desactivada_en": f.desactivada_en}
            for f in (await session.execute(select(ModeloIAVersionModel))).scalars()
        }
        eventos = (await session.execute(
            select(TraceabilityRecordModel.tipo_evento, TraceabilityRecordModel.payload,
                   TraceabilityRecordModel.timestamp)
            .where(
                TraceabilityRecordModel.chain_id == "SISTEMA",
                TraceabilityRecordModel.tipo_evento.in_((EVENTO_VERSION_ACTIVADA, EVENTO_VERSION_DESACTIVADA)),
            )
            .order_by(TraceabilityRecordModel.chain_seq)
        )).all()
        vigente = await SQLAlchemyModeloIARepository(session).obtener_version_activa_en(datetime.now(tz=timezone.utc))
        integra = (await VerificarIntegridadRegistroUseCase(
            SQLAlchemyTrazabilidadRepository(session)
        ).execute(chain_id="SISTEMA")).integra
    return {"filas": filas, "eventos": eventos, "vigente": vigente.version if vigente else None, "integra": integra}


def _assert_coherente(antes: dict, despues: dict) -> None:
    activas = [v for v, fila in despues["filas"].items() if fila["activa"]]
    assert len(activas) <= 1
    activa = activas[0] if activas else None
    # HU-47 esc. 2 responde lo mismo que la base.
    assert despues["vigente"] == activa
    # El último evento de la cadena (por chain_seq) también.
    tipo, payload, _ = despues["eventos"][-1]
    assert (payload["version_nueva"] if tipo == EVENTO_VERSION_ACTIVADA else None) == activa
    # Las horas de los cambios no retroceden en el orden de la cadena...
    horas = [hora for _, _, hora in despues["eventos"]]
    assert horas == sorted(horas)
    # ...ni en las columnas de cada versión.
    for version, fila in despues["filas"].items():
        for campo in ("activada_en", "desactivada_en"):
            previa, nueva = antes["filas"][version][campo], fila[campo]
            if previa is not None and nueva is not None:
                assert nueva >= previa, (version, campo)
        if not fila["activa"] and fila["desactivada_en"] is not None and fila["activada_en"] is not None:
            assert fila["desactivada_en"] >= fila["activada_en"], version
    assert despues["integra"] is True


async def _par_forzado(primera, segunda) -> tuple[str, str]:
    """`primera` se detiene después de tomar su hora; `segunda` corre entera
    mientras tanto (o espera el candado de `primera`)."""
    detenida, segunda_termino = asyncio.Event(), asyncio.Event()

    async def correr_primera() -> str:
        try:
            return await primera(lambda session: _RepositorioQueSeDetiene(session, detenida, segunda_termino))
        finally:
            detenida.set()  # si falló antes de detenerse, la segunda no queda esperando

    async def correr_segunda() -> str:
        await detenida.wait()
        try:
            return await segunda(SQLAlchemyModeloIARepository)
        finally:
            segunda_termino.set()

    return tuple(await asyncio.gather(correr_primera(), correr_segunda()))


ESCENARIOS_FORZADOS = {
    # desactivar(X) toma su hora; activar(X) termina en medio.
    "desactivar_x_y_en_medio_activar_x": ("desactivar", X, "activar", X, ("200", "200")),
    # activar(X) toma su hora; desactivar(X) termina en medio.
    "activar_x_y_en_medio_desactivar_x": ("activar", X, "desactivar", X, ("200", "200")),
    # activar(Y) toma su hora; desactivar(X) confirma en medio.
    "activar_y_y_en_medio_desactivar_x": ("activar", Y, "desactivar", X, ("200", "409")),
    # desactivar(X) toma su hora; activar(Y) termina en medio.
    "desactivar_x_y_en_medio_activar_y": ("desactivar", X, "activar", Y, ("200", "200")),
}


@pytest.mark.parametrize("escenario", list(ESCENARIOS_FORZADOS))
def test_intercalado_forzado_deja_historia_y_base_de_acuerdo(entorno, escenario):
    dsn, admin_id = entorno
    op_1, version_1, op_2, version_2, esperado = ESCENARIOS_FORZADOS[escenario]
    operaciones = {"activar": _activar, "desactivar": _desactivar}

    async def flujo():
        motor = create_async_engine(a_asyncpg(dsn), pool_size=4)
        fabrica = async_sessionmaker(motor, expire_on_commit=False)
        try:
            for _ in range(REPETICIONES_FORZADAS):
                await _dejar_x_activa(fabrica, admin_id)
                antes = await _estado(fabrica)
                resultados = await _par_forzado(
                    partial(operaciones[op_1], fabrica, admin_id, version_1),
                    partial(operaciones[op_2], fabrica, admin_id, version_2),
                )
                despues = await _estado(fabrica)
                _assert_coherente(antes, despues)
                # Con el candado la segunda espera a la primera: resultado fijo.
                assert resultados == esperado
        finally:
            await motor.dispose()

    asyncio.run(flujo())


@pytest.mark.parametrize("version_activar", [X, Y])
def test_pares_simultaneos_libres_quedan_coherentes(entorno, version_activar):
    dsn, admin_id = entorno

    async def flujo():
        motor = create_async_engine(a_asyncpg(dsn), pool_size=4)
        fabrica = async_sessionmaker(motor, expire_on_commit=False)
        try:
            for _ in range(REPETICIONES_LIBRES):
                await _dejar_x_activa(fabrica, admin_id)
                antes = await _estado(fabrica)
                activar, desactivar = await asyncio.gather(
                    _activar(fabrica, admin_id, version_activar, SQLAlchemyModeloIARepository),
                    _desactivar(fabrica, admin_id, X, SQLAlchemyModeloIARepository),
                )
                despues = await _estado(fabrica)
                _assert_coherente(antes, despues)
                # Activar siempre tiene éxito; desactivar(X) solo pierde si
                # activar(Y) la reemplazó antes (200/409); si no, 200/200.
                assert activar == "200"
                assert desactivar == "200" or (version_activar == Y and desactivar == "409")
        finally:
            await motor.dispose()

    asyncio.run(flujo())
