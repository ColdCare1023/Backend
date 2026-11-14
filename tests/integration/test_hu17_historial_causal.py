"""HU-17 esc. 1 contra la base: el historial que alimenta las features de la
lectura t termina estrictamente antes de t, también cuando la lectura llega
tarde desde el buffer LittleFS del nodo y ya hay lecturas posteriores."""

from datetime import datetime, timedelta, timezone

import pytest

from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.application.use_cases.registrar_lectura_termica import RegistrarLecturaTermicaUseCase
from src.domain.entities.lectura_termica import LecturaTermica
from src.infrastructure.ai.random_forest_service import get_random_forest_service
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)

DEVICE_ID = "FARM-HU17-01"
_BASE = datetime.now(tz=timezone.utc).replace(second=0, microsecond=0) - timedelta(hours=2)


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


def _use_case(session) -> RegistrarLecturaTermicaUseCase:
    return RegistrarLecturaTermicaUseCase(
        SQLAlchemyLecturaRepository(session),
        SQLAlchemyAlertaRepository(session),
        SQLAlchemyTrazabilidadRepository(session),
        ClasificarRiesgoTermicoUseCase(get_random_forest_service(), "experimental"),
        device_repository=SQLAlchemyDeviceRepository(session),
        registro_dispositivos_estricto=False,
        ia_legado_modo="experimental",
    )


async def test_el_repositorio_corta_estrictamente_antes_de_hasta(db_session_factory):
    async with db_session_factory() as session:
        uc = _use_case(session)
        for minuto in range(6):
            await uc.execute(_lectura(minuto, 5.0))
        await session.commit()

    async with db_session_factory() as session:
        repo = SQLAlchemyLecturaRepository(session)
        corte = _BASE + timedelta(minutes=3)
        antes = await repo.listar_recientes_por_device(DEVICE_ID, limite=50, hasta=corte)
        todas = await repo.listar_recientes_por_device(DEVICE_ID, limite=50)

    def _utc(valor):
        return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)

    assert [_utc(lec.timestamp) for lec in antes] == [_BASE + timedelta(minutes=m) for m in (2, 1, 0)]
    assert len(todas) == 6  # sin `hasta`, el comportamiento anterior


async def test_lectura_reenviada_del_buffer_no_ve_las_posteriores(db_session_factory):
    # El nodo sincronizó primero lo más reciente (dos lecturas fuera de rango)
    # y después reenvía una lectura anterior que quedó en LittleFS.
    async with db_session_factory() as session:
        uc = _use_case(session)
        for minuto, temperatura in ((0, 5.0), (1, 5.0), (2, 5.0), (5, 9.5), (6, 9.6)):
            await uc.execute(_lectura(minuto, temperatura))
        await session.commit()

    async with db_session_factory() as session:
        diferida = await _use_case(session).execute(_lectura(3, 5.0))
        await session.commit()

    assert diferida.estado_inferencia == "completada"
    vector = diferida.vector_features_ia
    # Sin el tope, las dos excursiones POSTERIORES contaban como desviaciones previas.
    assert vector["frecuencia_desviaciones"] == 0.0
    assert vector["duracion_fuera_rango"] == 0.0
    assert vector["tendencia_termica"] == pytest.approx(0.0, abs=1e-9)
