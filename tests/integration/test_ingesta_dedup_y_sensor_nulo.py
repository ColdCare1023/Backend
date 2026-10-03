"""Pruebas de los hallazgos B-04 (deduplicación MQTT) y B-05 (sensor sin
lectura tratado incorrectamente como 0.0 °C) de la auditoría.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.application.use_cases.registrar_lectura_termica import RegistrarLecturaTermicaUseCase
from src.domain.entities.lectura_termica import LecturaTermica
from src.infrastructure.ai.random_forest_service import get_random_forest_service
from src.infrastructure.database.models import ThermalAlertModel, ThermalReadingModel
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)

DEVICE_ID = "FARM-DEDUP-01"


def _construir_use_case(session) -> RegistrarLecturaTermicaUseCase:
    return RegistrarLecturaTermicaUseCase(
        SQLAlchemyLecturaRepository(session),
        SQLAlchemyAlertaRepository(session),
        SQLAlchemyTrazabilidadRepository(session),
        ClasificarRiesgoTermicoUseCase(get_random_forest_service()),
        device_repository=SQLAlchemyDeviceRepository(session),
        registro_dispositivos_estricto=False,
    )


# Anclado al reloj actual, no a una fecha fija: la ingesta rechaza
# timestamps implausibles (B-10), así que una constante de calendario
# haría que estas pruebas empezaran a fallar con el paso del tiempo.
_BASE = datetime.now(tz=timezone.utc).replace(microsecond=0) - timedelta(hours=3)


def _lectura(**overrides) -> LecturaTermica:
    base = {
        "device_id": DEVICE_ID,
        "timestamp": _BASE,
        "temperatura_ambiental": 21.0,
        "humedad_ambiental": 55.0,
        "temperatura_interna": 5.0,
        "apertura_refrigerador": False,
        "estado_conectividad": "online",
    }
    base.update(overrides)
    return LecturaTermica(**base)


async def test_reenvio_mqtt_con_mismo_device_y_timestamp_no_duplica(db_session_factory):
    """Simula un reenvío QoS1 (PUBACK perdido): la misma lectura exacta llega
    dos veces. Debe persistirse una sola vez (idempotencia, corrige B-04)."""
    async with db_session_factory() as session:
        use_case = _construir_use_case(session)
        primera = await use_case.execute(_lectura())
        await session.commit()

    async with db_session_factory() as session:
        use_case = _construir_use_case(session)
        segunda = await use_case.execute(_lectura())
        await session.commit()

    assert segunda.id == primera.id

    async with db_session_factory() as session:
        conteo = (
            await session.execute(
                select(ThermalReadingModel).where(ThermalReadingModel.device_id == DEVICE_ID)
            )
        ).scalars().all()

    assert len(conteo) == 1, "El reenvío MQTT generó un registro duplicado."


async def test_lectura_con_temperaturas_distintas_mismo_instante_tambien_es_idempotente(
    db_session_factory,
):
    """Aunque el segundo mensaje trajera valores ligeramente distintos (ruido de
    transmisión), el criterio de deduplicación es (device_id, timestamp): el
    primer registro persistido es el que prevalece."""
    async with db_session_factory() as session:
        use_case = _construir_use_case(session)
        primera = await use_case.execute(_lectura(temperatura_interna=5.0))
        await session.commit()

    async with db_session_factory() as session:
        use_case = _construir_use_case(session)
        segunda = await use_case.execute(_lectura(temperatura_interna=5.3))
        await session.commit()

    assert segunda.id == primera.id
    assert segunda.temperatura_interna == primera.temperatura_interna == 5.0


async def test_sensor_temperatura_interna_none_no_se_convierte_en_cero(db_session_factory):
    """Corrige B-05: un sensor caído (None) debe quedar 'no clasificable', sin
    tratarse como 0.0 °C (que dispararía una excursión crítica falsa)."""
    async with db_session_factory() as session:
        use_case = _construir_use_case(session)
        lectura_guardada = await use_case.execute(
            _lectura(timestamp=_BASE + timedelta(hours=1), temperatura_interna=None)
        )
        await session.commit()

    assert lectura_guardada.nivel_riesgo is None

    async with db_session_factory() as session:
        alertas = (
            await session.execute(
                select(ThermalAlertModel).where(ThermalAlertModel.reading_id == lectura_guardada.id)
            )
        ).scalars().all()

    assert alertas == [], (
        "Una lectura sin dato de sensor no debe generar alerta de excursión crítica falsa."
    )


async def test_sensor_temperatura_interna_presente_sigue_clasificando_normalmente(
    db_session_factory,
):
    """Control: con temperatura real dentro de rango, la clasificación normal
    sigue funcionando (no se rompió el camino feliz al corregir B-05).
    HU-17: se siembran dos lecturas previas para que la IA tenga historial
    suficiente y calcule tendencia térmica en vez de quedar no_clasificable."""
    async with db_session_factory() as session:
        use_case = _construir_use_case(session)
        await use_case.execute(
            _lectura(timestamp=_BASE + timedelta(hours=2) - timedelta(minutes=10), temperatura_interna=5.0)
        )
        await session.commit()
    async with db_session_factory() as session:
        use_case = _construir_use_case(session)
        await use_case.execute(
            _lectura(timestamp=_BASE + timedelta(hours=2) - timedelta(minutes=5), temperatura_interna=5.0)
        )
        await session.commit()

    async with db_session_factory() as session:
        use_case = _construir_use_case(session)
        lectura_guardada = await use_case.execute(
            _lectura(timestamp=_BASE + timedelta(hours=2), temperatura_interna=5.0)
        )
        await session.commit()

    assert lectura_guardada.nivel_riesgo is not None
    assert lectura_guardada.nivel_riesgo.value == "normal"
