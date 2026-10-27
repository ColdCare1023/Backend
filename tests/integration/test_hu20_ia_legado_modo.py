"""HU-20 / B-1: modo del modelo Random Forest legado (IA_LEGADO_MODO).

El modelo legado se entrenó con escenarios sintéticos. En modo experimental
su clase queda como evidencia marcada, pero no abre alertas preventivas; en
modo desactivado ni siquiera se ejecuta. La excursión confirmada por rango
2-8 °C (HU-21) no depende del modo.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.application.use_cases.registrar_lectura_termica import RegistrarLecturaTermicaUseCase
from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.ai.random_forest_service import (
    ESTADO_COMPLETADA,
    ORIGEN_MODELO,
    ORIGEN_REGLA_SALVAGUARDA,
    ResultadoInferencia,
)
from src.infrastructure.ai.reglas_riesgo import clasificar_por_regla
from src.infrastructure.database.models import ThermalAlertModel
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.interface.api.mappers import lectura_to_response

DEVICE_ID = "FARM-HU20-01"
_BASE = datetime.now(tz=timezone.utc).replace(microsecond=0) - timedelta(hours=2)


class _ModeloQueSiempreAnticipa:
    """Doble del Random Forest legado: predice riesgo_preventivo siempre."""

    metadata = {"model_version": "legado-sintetico"}

    def __init__(self) -> None:
        self.llamadas = 0

    def inferir(self, features) -> ResultadoInferencia:
        self.llamadas += 1
        return ResultadoInferencia(
            NivelRiesgo.RIESGO_PREVENTIVO, 0.9, ORIGEN_MODELO, ESTADO_COMPLETADA,
            probabilidades_por_clase={"normal": 0.1, "riesgo_preventivo": 0.9},
            vector_features={"temperatura_interna": features.temperatura_interna},
            nivel_regla=clasificar_por_regla(features),
        )

    def inferir_solo_regla(self, features) -> ResultadoInferencia:
        nivel = clasificar_por_regla(features)
        return ResultadoInferencia(nivel, None, ORIGEN_REGLA_SALVAGUARDA, "modelo_no_disponible",
                                   "modelo_legado_desactivado", nivel_regla=nivel)


def _use_case(session, modelo, modo: str) -> RegistrarLecturaTermicaUseCase:
    return RegistrarLecturaTermicaUseCase(
        SQLAlchemyLecturaRepository(session),
        SQLAlchemyAlertaRepository(session),
        SQLAlchemyTrazabilidadRepository(session),
        ClasificarRiesgoTermicoUseCase(modelo, modo),
        device_repository=SQLAlchemyDeviceRepository(session),
        registro_dispositivos_estricto=False,
        ia_legado_modo=modo,
    )


def _lectura(minuto: int, temperatura: float) -> LecturaTermica:
    return LecturaTermica(
        device_id=DEVICE_ID,
        timestamp=_BASE + timedelta(minutes=minuto),
        temperatura_ambiental=21.0,
        humedad_ambiental=55.0,
        temperatura_interna=temperatura,
        apertura_refrigerador=False,
        estado_conectividad="online",
    )


async def _registrar(db_session_factory, modelo, modo: str, temperaturas: list[float]) -> list[LecturaTermica]:
    guardadas = []
    async with db_session_factory() as session:
        use_case = _use_case(session, modelo, modo)
        for minuto, temperatura in enumerate(temperaturas):
            guardadas.append(await use_case.execute(_lectura(minuto, temperatura)))
        await session.commit()
    return guardadas


async def _alertas(db_session_factory) -> list[ThermalAlertModel]:
    async with db_session_factory() as session:
        return (await session.execute(select(ThermalAlertModel))).scalars().all()


async def test_experimental_marca_la_clase_y_no_abre_alerta_preventiva(db_session_factory):
    # 5 °C: en pleno rango, lejos de los límites; la salvaguarda dice normal.
    lecturas = await _registrar(db_session_factory, _ModeloQueSiempreAnticipa(), "experimental", [5.0, 5.0, 5.0])

    ultima = lecturas[-1]
    assert ultima.nivel_riesgo == NivelRiesgo.RIESGO_PREVENTIVO  # evidencia cruda del modelo
    assert ultima.riesgo_efectivo == NivelRiesgo.NORMAL
    assert lectura_to_response(ultima).ia_experimental is True
    assert await _alertas(db_session_factory) == []


async def test_activo_conserva_el_comportamiento_anterior(db_session_factory):
    lecturas = await _registrar(db_session_factory, _ModeloQueSiempreAnticipa(), "activo", [5.0, 5.0, 5.0])

    assert lecturas[-1].riesgo_efectivo == NivelRiesgo.RIESGO_PREVENTIVO
    assert lectura_to_response(lecturas[-1]).ia_experimental is False
    alertas = await _alertas(db_session_factory)
    assert [a.nivel_riesgo for a in alertas] == [NivelRiesgo.RIESGO_PREVENTIVO.value]


async def test_desactivado_no_ejecuta_el_modelo(db_session_factory):
    modelo = _ModeloQueSiempreAnticipa()
    lecturas = await _registrar(db_session_factory, modelo, "desactivado", [5.0, 5.0, 5.0])

    assert modelo.llamadas == 0
    assert lecturas[-1].riesgo_efectivo == NivelRiesgo.NORMAL
    assert lecturas[-1].modelo_version is None
    assert lecturas[-1].motivo_no_inferencia == "modelo_legado_desactivado"
    assert await _alertas(db_session_factory) == []


@pytest.mark.parametrize("modo", ["experimental", "desactivado"])
async def test_la_excursion_confirmada_no_depende_del_modo(db_session_factory, modo):
    lecturas = await _registrar(db_session_factory, _ModeloQueSiempreAnticipa(), modo, [5.0, 5.0, 9.5])

    assert lecturas[-1].excursion_confirmada is True
    assert lecturas[-1].riesgo_efectivo == NivelRiesgo.EXCURSION_CRITICA
    alertas = await _alertas(db_session_factory)
    assert [a.nivel_riesgo for a in alertas] == [NivelRiesgo.EXCURSION_CRITICA.value]


async def test_experimental_respeta_la_salvaguarda_cerca_del_limite(db_session_factory):
    # 7,6 °C: a menos de 1 °C del límite superior; la regla determinista sí
    # anticipa y su alerta preventiva no depende del modelo legado.
    lecturas = await _registrar(db_session_factory, _ModeloQueSiempreAnticipa(), "experimental", [5.0, 5.0, 7.6])

    assert lecturas[-1].riesgo_efectivo == NivelRiesgo.RIESGO_PREVENTIVO
    alertas = await _alertas(db_session_factory)
    assert [a.nivel_riesgo for a in alertas] == [NivelRiesgo.RIESGO_PREVENTIVO.value]
