"""Outcome 1 (RIE-01, RIE-03, LAT-02, TEM-05): la alerta crítica nunca depende de la IA.

La inferencia corre en un hilo con tope de tiempo. Si se cuelga o lanza una
excepción, la lectura queda con estado_inferencia=fallida y la regla 2-8 °C
sigue abriendo la alerta crítica.
"""

import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.application.use_cases.registrar_lectura_termica import RegistrarLecturaTermicaUseCase
from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.ai.random_forest_service import ESTADO_FALLIDA
from src.infrastructure.config import Settings
from src.infrastructure.database.models import ThermalAlertModel
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)

DEVICE_ID = "FARM-OUT1-01"
_BASE = datetime.now(tz=timezone.utc).replace(microsecond=0) - timedelta(hours=2)


class _ModeloLento:
    metadata = {"model_version": "lento"}

    def inferir(self, features):
        time.sleep(5)  # bloquea el hilo, no el bucle de eventos
        raise AssertionError("no debía esperarse")

    inferir_solo_regla = inferir


class _ModeloQueLanza:
    metadata = {"model_version": "roto"}

    def inferir(self, features):
        raise RuntimeError("fallo interno del modelo con datos que no deben filtrarse")

    inferir_solo_regla = inferir


def _use_case(session, modelo, **kwargs) -> RegistrarLecturaTermicaUseCase:
    return RegistrarLecturaTermicaUseCase(
        SQLAlchemyLecturaRepository(session),
        SQLAlchemyAlertaRepository(session),
        SQLAlchemyTrazabilidadRepository(session),
        ClasificarRiesgoTermicoUseCase(modelo, "activo"),
        device_repository=SQLAlchemyDeviceRepository(session),
        registro_dispositivos_estricto=False,
        ia_legado_modo="activo",
        **kwargs,
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


async def _registrar(db_session_factory, modelo, temperaturas, **kwargs) -> list[LecturaTermica]:
    guardadas = []
    async with db_session_factory() as session:
        use_case = _use_case(session, modelo, **kwargs)
        for minuto, temperatura in enumerate(temperaturas):
            guardadas.append(await use_case.execute(_lectura(minuto, temperatura)))
        await session.commit()
    return guardadas


async def _alertas(db_session_factory) -> list[ThermalAlertModel]:
    async with db_session_factory() as session:
        return (await session.execute(select(ThermalAlertModel))).scalars().all()


async def test_inferencia_colgada_vence_en_una_lectura_normal(db_session_factory):
    inicio = time.monotonic()
    lecturas = await _registrar(db_session_factory, _ModeloLento(), [5.0, 5.0, 5.0], ia_timeout_s=0.2)

    assert time.monotonic() - inicio < 3  # no esperó los 5 s del modelo
    assert lecturas[-1].estado_inferencia == ESTADO_FALLIDA
    assert lecturas[-1].motivo_no_inferencia == "timeout_inferencia"
    assert lecturas[-1].nivel_riesgo is None
    assert lecturas[-1].riesgo_efectivo == NivelRiesgo.NORMAL


async def test_la_excursion_sale_aunque_la_ia_este_colgada(db_session_factory):
    # La lectura fuera de rango no consulta al modelo (RF-08): ni espera ni falla.
    inicio = time.monotonic()
    lecturas = await _registrar(
        db_session_factory, _ModeloLento(), [5.0, 9.5], ia_timeout_s=0.2, ia_prueba_retardo_s=1.0
    )

    ultima = lecturas[-1]
    assert time.monotonic() - inicio < 3
    assert ultima.estado_inferencia != ESTADO_FALLIDA  # la IA ni se intentó
    assert ultima.excursion_confirmada is True
    assert ultima.riesgo_efectivo == NivelRiesgo.EXCURSION_CRITICA
    assert [a.nivel_riesgo for a in await _alertas(db_session_factory)] == [NivelRiesgo.EXCURSION_CRITICA.value]


async def test_excepcion_del_modelo_no_filtra_el_mensaje(db_session_factory):
    lecturas = await _registrar(db_session_factory, _ModeloQueLanza(), [5.0, 5.0, 5.0], ia_timeout_s=1.0)

    assert lecturas[-1].estado_inferencia == ESTADO_FALLIDA
    assert lecturas[-1].motivo_no_inferencia == "excepcion_inferencia"
    assert "datos que no deben filtrarse" not in (lecturas[-1].motivo_no_inferencia or "")


async def test_opciones_de_prueba_simulan_retardo_y_excepcion(db_session_factory):
    class _Normal:
        metadata = {"model_version": "ok"}

    retardo = await _registrar(
        db_session_factory, _Normal(), [5.0, 5.0, 5.0], ia_timeout_s=0.1, ia_prueba_retardo_s=1.0
    )
    assert retardo[-1].motivo_no_inferencia == "timeout_inferencia"


async def test_excepcion_inyectada(db_session_factory):
    class _Normal:
        metadata = {"model_version": "ok"}

    lecturas = await _registrar(
        db_session_factory, _Normal(), [5.0, 5.0, 5.0], ia_timeout_s=1.0, ia_prueba_excepcion=True
    )
    assert lecturas[-1].estado_inferencia == ESTADO_FALLIDA
    assert lecturas[-1].motivo_no_inferencia == "excepcion_inferencia"


@pytest.mark.parametrize("opcion", [{"ia_prueba_retardo_s": 2.0}, {"ia_prueba_excepcion": True}])
def test_las_opciones_de_falla_no_arrancan_en_produccion(opcion, monkeypatch):
    for k in ("JWT_SECRET_KEY",):
        monkeypatch.setenv(k, "x" * 48)
    with pytest.raises(ValueError, match="no puede activarse con ENVIRONMENT=production"):
        Settings(environment="production", _env_file=None, **opcion)
