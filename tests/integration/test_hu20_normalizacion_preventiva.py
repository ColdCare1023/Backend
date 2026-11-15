"""HU-20 esc. 2, 3 y 4.

- Esc. 3: una alerta preventiva se resuelve solo cuando el riesgo lleva la
  ventana de normalización completa en Normal; una lectura preventiva reinicia
  la cuenta. La excursión crítica sigue cerrándose con la primera lectura en
  rango (HU-21 esc. 2).
- Esc. 4: cada transición deja en la cadena estado anterior, estado nuevo,
  proceso que la originó y el tipo de transición; el cierre, su motivo.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.application.use_cases.generar_alerta import (
    CIERRE_CAMBIO_NIVEL,
    CIERRE_NORMALIZACION,
    CIERRE_RECUPERACION,
    TRANSICION_ACTUALIZACION,
    TRANSICION_CREACION,
    TRANSICION_NORMALIZACION,
    TRANSICION_REAPERTURA,
    GenerarAlertaUseCase,
)
from src.application.use_cases.registrar_lectura_termica import RegistrarLecturaTermicaUseCase
from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.ai.random_forest_service import ESTADO_COMPLETADA, ORIGEN_MODELO, ResultadoInferencia
from src.infrastructure.config import Settings
from src.infrastructure.database.models import ThermalAlertModel, TraceabilityRecordModel
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.interface.api.mappers import alerta_to_response

DEVICE_ID = "FARM-HU20-NORM"
T0 = datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc)
P, N, C = NivelRiesgo.RIESGO_PREVENTIVO, NivelRiesgo.NORMAL, NivelRiesgo.EXCURSION_CRITICA


class _RepoEnMemoria:
    def __init__(self):
        self.filas: list = []

    async def agregar(self, alerta):
        alerta.id = uuid4()
        self.filas.append(alerta)
        return alerta

    async def actualizar(self, alerta):
        return alerta

    async def obtener_episodio_abierto(self, device_id):
        return next((a for a in self.filas if a.device_id == device_id and a.episodio_abierto), None)

    async def obtener_ultimo_cerrado(self, device_id, nivel_riesgo):
        cerrados = [
            a for a in self.filas if a.device_id == device_id and a.nivel_riesgo == nivel_riesgo and not a.episodio_abierto
        ]
        return cerrados[-1] if cerrados else None


async def _secuencia(uc: GenerarAlertaUseCase, pasos: list[tuple[int, NivelRiesgo]]) -> list[str | None]:
    transiciones = []
    for minuto, nivel in pasos:
        await uc.execute(reading_id=uuid4(), device_id=DEVICE_ID, nivel_riesgo=nivel,
                         timestamp=T0 + timedelta(minutes=minuto))
        transiciones.append(uc.transicion)
    return transiciones


# ── Esc. 3: ventana de normalización (regla de negocio aislada) ─────────────


async def test_la_preventiva_se_resuelve_solo_tras_la_ventana_completa_en_normal():
    repo = _RepoEnMemoria()
    uc = GenerarAlertaUseCase(repo, minutos_normales_preventiva=10)

    transiciones = await _secuencia(uc, [(0, P), (1, P), (2, N), (6, N), (11, N)])
    (alerta,) = repo.filas
    assert transiciones[:4] == [
        TRANSICION_CREACION, TRANSICION_ACTUALIZACION, TRANSICION_NORMALIZACION, TRANSICION_NORMALIZACION,
    ]
    assert alerta.episodio_abierto is True  # 9 min en Normal: todavía no
    assert alerta.normalizacion_desde == T0 + timedelta(minutes=2)

    await _secuencia(uc, [(12, N)])
    assert alerta.episodio_abierto is False
    assert alerta.cerrada_en == T0 + timedelta(minutes=12)
    assert uc.motivos_cierre[alerta.id] == CIERRE_NORMALIZACION
    assert alerta.normalizacion_desde == T0 + timedelta(minutes=2)  # se conserva como evidencia


async def test_una_lectura_preventiva_reinicia_la_ventana():
    repo = _RepoEnMemoria()
    uc = GenerarAlertaUseCase(repo, minutos_normales_preventiva=10)

    await _secuencia(uc, [(0, P), (2, N), (9, P)])
    (alerta,) = repo.filas
    assert alerta.normalizacion_desde is None and alerta.episodio_abierto is True

    await _secuencia(uc, [(10, N), (15, N), (19, N)])
    assert alerta.episodio_abierto is True  # la cuenta empezó de nuevo en el minuto 10
    await _secuencia(uc, [(20, N)])
    assert alerta.episodio_abierto is False
    assert len(repo.filas) == 1  # nunca se duplicó la alerta (esc. 2)


async def test_la_excursion_critica_se_cierra_con_la_primera_lectura_en_rango():
    repo = _RepoEnMemoria()
    uc = GenerarAlertaUseCase(repo, minutos_normales_preventiva=10)

    await _secuencia(uc, [(0, C), (1, N)])

    (alerta,) = repo.filas
    assert alerta.episodio_abierto is False
    assert alerta.normalizacion_desde is None
    assert uc.motivos_cierre[alerta.id] == CIERRE_RECUPERACION


async def test_escalar_a_critica_durante_la_normalizacion_cierra_la_preventiva():
    repo = _RepoEnMemoria()
    uc = GenerarAlertaUseCase(repo, minutos_normales_preventiva=10)

    await _secuencia(uc, [(0, P), (1, N), (3, C)])

    preventiva, critica = repo.filas
    assert preventiva.episodio_abierto is False
    assert uc.motivos_cierre[preventiva.id] == CIERRE_CAMBIO_NIVEL
    assert critica.nivel_riesgo == C and critica.episodio_abierto is True


async def test_ventana_cero_conserva_el_cierre_inmediato():
    repo = _RepoEnMemoria()
    uc = GenerarAlertaUseCase(repo, minutos_normales_preventiva=0)

    await _secuencia(uc, [(0, P), (1, N)])

    assert repo.filas[0].episodio_abierto is False
    assert uc.motivos_cierre[repo.filas[0].id] == CIERRE_RECUPERACION


async def test_reaparecer_dentro_del_cooldown_reabre_la_misma_alerta():
    repo = _RepoEnMemoria()
    uc = GenerarAlertaUseCase(repo, minutos_normales_preventiva=5)

    transiciones = await _secuencia(uc, [(0, P), (1, N), (6, N), (8, P)])

    assert len(repo.filas) == 1
    assert transiciones[-1] == TRANSICION_REAPERTURA
    assert repo.filas[0].episodio_abierto is True and repo.filas[0].normalizacion_desde is None


def test_el_valor_por_defecto_de_la_ventana():
    assert Settings(jwt_secret_key="x" * 40).alerta_preventiva_minutos_normales == 10


# ── Esc. 3 y 4 extremo a extremo: base de datos y cadena ────────────────────


class _ModeloPorUmbral:
    """Doble del legado «activo»: preventivo desde 7 °C, normal por debajo."""

    metadata = {"model_version": "doble-umbral"}

    def inferir(self, features) -> ResultadoInferencia:
        nivel = P if features.temperatura_interna >= 7.0 else N
        return ResultadoInferencia(nivel, 0.9, ORIGEN_MODELO, ESTADO_COMPLETADA, nivel_regla=nivel)


_BASE = datetime.now(tz=timezone.utc).replace(second=0, microsecond=0) - timedelta(hours=1)


async def _registrar(db_session_factory, pasos: list[tuple[int, float]]) -> None:
    async with db_session_factory() as session:
        uc = RegistrarLecturaTermicaUseCase(
            SQLAlchemyLecturaRepository(session), SQLAlchemyAlertaRepository(session),
            SQLAlchemyTrazabilidadRepository(session), ClasificarRiesgoTermicoUseCase(_ModeloPorUmbral(), "activo"),
            device_repository=SQLAlchemyDeviceRepository(session), registro_dispositivos_estricto=False,
            ia_legado_modo="activo", minutos_normales_preventiva=10,
        )
        for minuto, temperatura in pasos:
            await uc.execute(LecturaTermica(
                device_id=DEVICE_ID, timestamp=_BASE + timedelta(minutes=minuto), temperatura_ambiental=21.0,
                humedad_ambiental=55.0, temperatura_interna=temperatura, apertura_refrigerador=False,
                estado_conectividad="online",
            ))
        await session.commit()


async def _eventos(db_session_factory, tipo: str) -> list[dict]:
    async with db_session_factory() as session:
        filas = (await session.execute(
            select(TraceabilityRecordModel).order_by(TraceabilityRecordModel.chain_seq)
        )).scalars().all()
    return [f.payload for f in filas if f.tipo_evento == tipo]


async def _alerta(db_session_factory) -> ThermalAlertModel:
    async with db_session_factory() as session:
        (alerta,) = (await session.execute(select(ThermalAlertModel))).scalars().all()
    return alerta


async def test_transiciones_de_la_preventiva_quedan_en_la_cadena(db_session_factory):
    await _registrar(db_session_factory, [(0, 5.0), (1, 5.0), (2, 7.5), (3, 7.6), (4, 5.0), (10, 5.0)])

    alerta = await _alerta(db_session_factory)
    assert alerta.episodio_abierto == 1  # 6 min en Normal: sigue abierta
    assert alerta.normalizacion_desde is not None
    creacion, actualizacion = await _eventos(db_session_factory, "ALERTA_TERMICA")
    assert creacion["transicion"] == TRANSICION_CREACION
    assert creacion["estado_anterior"] is None and creacion["estado_nuevo"] == "pendiente"
    assert creacion["origen_transicion"] == "motor_riesgo"
    assert creacion["nivel_riesgo"] == "riesgo_preventivo"
    assert creacion["alerta_id"] == str(alerta.id)
    assert actualizacion["transicion"] == TRANSICION_ACTUALIZACION
    assert actualizacion["estado_anterior"] == "pendiente" and actualizacion["estado_nuevo"] == "pendiente"
    assert await _eventos(db_session_factory, "ALERTA_CERRADA") == []

    await _registrar(db_session_factory, [(14, 5.0)])

    alerta = await _alerta(db_session_factory)
    assert alerta.episodio_abierto is None and alerta.cerrada_en is not None
    (cierre,) = await _eventos(db_session_factory, "ALERTA_CERRADA")
    assert cierre["alerta_id"] == str(alerta.id)
    assert cierre["estado_anterior"] == "episodio_abierto" and cierre["estado_nuevo"] == "episodio_cerrado"
    assert cierre["origen_transicion"] == "motor_riesgo"
    assert cierre["motivo_cierre"] == CIERRE_NORMALIZACION
    assert cierre["minutos_normales_requeridos"] == 10
    assert cierre["normalizacion_desde"].startswith((_BASE + timedelta(minutes=4)).strftime("%Y-%m-%dT%H:%M"))


async def test_la_api_expone_la_normalizacion_en_curso(db_session_factory):
    await _registrar(db_session_factory, [(0, 5.0), (1, 5.0), (2, 7.5), (3, 5.0)])

    async with db_session_factory() as session:
        (alerta,) = await SQLAlchemyAlertaRepository(session).listar(device_id=DEVICE_ID)
    respuesta = alerta_to_response(alerta)

    assert respuesta.episodio_abierto is True
    assert respuesta.normalizacion_desde is not None
    assert respuesta.normalizacion_desde.replace(tzinfo=timezone.utc) == _BASE + timedelta(minutes=3)


@pytest.mark.parametrize("ventana", [0, 10])
async def test_cierre_de_excursion_critica_registra_su_motivo(db_session_factory, ventana):
    async with db_session_factory() as session:
        uc = RegistrarLecturaTermicaUseCase(
            SQLAlchemyLecturaRepository(session), SQLAlchemyAlertaRepository(session),
            SQLAlchemyTrazabilidadRepository(session), ClasificarRiesgoTermicoUseCase(_ModeloPorUmbral(), "activo"),
            device_repository=SQLAlchemyDeviceRepository(session), registro_dispositivos_estricto=False,
            ia_legado_modo="activo", minutos_normales_preventiva=ventana,
        )
        for minuto, temperatura in ((0, 5.0), (1, 9.5), (2, 5.0)):
            await uc.execute(LecturaTermica(
                device_id=DEVICE_ID, timestamp=_BASE + timedelta(minutes=minuto), temperatura_ambiental=21.0,
                humedad_ambiental=55.0, temperatura_interna=temperatura, apertura_refrigerador=False,
                estado_conectividad="online",
            ))
        await session.commit()

    (cierre,) = await _eventos(db_session_factory, "ALERTA_CERRADA")
    assert cierre["nivel_riesgo"] == "excursion_critica"
    assert cierre["motivo_cierre"] == CIERRE_RECUPERACION
    assert cierre["minutos_normales_requeridos"] is None
