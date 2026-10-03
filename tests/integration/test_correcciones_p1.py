"""Pruebas de la fase de corrección P1: AIV-02 (tormenta de alertas), AIV-03
(guard completo de sensores) y AIV-07 (confianza_ia nunca 0.0 como centinela).
"""

import math
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.application.use_cases.registrar_lectura_termica import RegistrarLecturaTermicaUseCase
from src.domain.entities.lectura_termica import LecturaTermica
from src.infrastructure.ai.random_forest_service import get_random_forest_service
from src.infrastructure.database.models import ThermalAlertModel
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)

DEVICE_ID = "FARM-P1-01"


def _construir_use_case(session) -> RegistrarLecturaTermicaUseCase:
    return RegistrarLecturaTermicaUseCase(
        SQLAlchemyLecturaRepository(session),
        SQLAlchemyAlertaRepository(session),
        SQLAlchemyTrazabilidadRepository(session),
        ClasificarRiesgoTermicoUseCase(get_random_forest_service()),
        device_repository=SQLAlchemyDeviceRepository(session),
        registro_dispositivos_estricto=False,
    )


# Ver nota en test_ingesta_dedup_y_sensor_nulo: base relativa al reloj
# actual para que la validación de timestamp (B-10) no invalide la prueba.
_BASE = datetime.now(tz=timezone.utc).replace(second=0, microsecond=0) - timedelta(hours=2)


def _lectura(minuto: int, **overrides) -> LecturaTermica:
    base = {
        "device_id": DEVICE_ID,
        "timestamp": _BASE + timedelta(minutes=minuto),
        "temperatura_ambiental": 21.0,
        "humedad_ambiental": 55.0,
        "temperatura_interna": 15.0,  # fuera de rango BPA 2-8°C
        "apertura_refrigerador": False,
        "estado_conectividad": "online",
    }
    base.update(overrides)
    return LecturaTermica(**base)


# ── AIV-02: tormenta de alertas ──────────────────────────────────────────


async def test_lecturas_criticas_consecutivas_no_generan_alertas_duplicadas(db_session_factory):
    """5 lecturas críticas consecutivas del mismo dispositivo deben mantener
    UNA sola fila de alerta abierta, actualizada, no 5 filas nuevas."""
    for minuto in range(5):
        async with db_session_factory() as session:
            use_case = _construir_use_case(session)
            await use_case.execute(
                _lectura(minuto, temperatura_interna=15.0)
            )
            await session.commit()

    async with db_session_factory() as session:
        alertas = (
            await session.execute(select(ThermalAlertModel).where(ThermalAlertModel.device_id == DEVICE_ID))
        ).scalars().all()

    assert len(alertas) == 1, "Se generaron múltiples alertas para el mismo episodio (tormenta de alertas)."
    assert alertas[0].episodio_abierto == 1


async def test_recuperacion_a_normal_cierra_el_episodio():
    """Prueba unitaria de GenerarAlertaUseCase con un repositorio en memoria:
    aísla la regla de negocio (recuperación cierra el episodio) de la
    variabilidad legítima del Random Forest real sobre lecturas límite con
    historial reciente de desviaciones (el modelo puede, correctamente,
    seguir marcando riesgo_preventivo en la primera lectura normal tras una
    excursión — eso no invalida la regla de cierre de episodio en sí, que se
    prueba aquí de forma determinista)."""
    from uuid import uuid4

    from src.application.use_cases.generar_alerta import GenerarAlertaUseCase
    from src.domain.value_objects.nivel_riesgo import NivelRiesgo

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
            for a in self.filas:
                if a.device_id == device_id and a.episodio_abierto:
                    return a
            return None

        async def obtener_ultimo_cerrado(self, device_id, nivel_riesgo):
            cerrados = [
                a for a in self.filas
                if a.device_id == device_id and a.nivel_riesgo == nivel_riesgo and not a.episodio_abierto
            ]
            return cerrados[-1] if cerrados else None

    repo = _RepoEnMemoria()
    uc = GenerarAlertaUseCase(repo)
    ts0 = datetime(2026, 7, 22, 10, 0, 0, tzinfo=timezone.utc)

    abierta = await uc.execute(reading_id=uuid4(), device_id=DEVICE_ID, nivel_riesgo=NivelRiesgo.EXCURSION_CRITICA, timestamp=ts0)
    assert abierta.episodio_abierto is True
    assert len(repo.filas) == 1

    resultado = await uc.execute(
        reading_id=uuid4(), device_id=DEVICE_ID, nivel_riesgo=NivelRiesgo.NORMAL,
        timestamp=ts0 + timedelta(minutes=1),
    )
    assert resultado is None, "La recuperación a NORMAL no debe crear una fila de alerta nueva."
    assert len(repo.filas) == 1, "No debe crearse una segunda alerta al recuperarse."
    assert repo.filas[0].episodio_abierto is False
    assert repo.filas[0].cerrada_en is not None


async def test_escalamiento_preventivo_a_critico_cierra_y_abre_nuevo_episodio(db_session_factory):
    """HU-18/21/34: 9.0 °C ya está fuera de 2-8 °C, así que con la regla
    directa (HU-21) es una excursión CONFIRMADA de inmediato, no un riesgo
    preventivo — a diferencia del comportamiento previo, que solo escalaba a
    crítico según duración/distancia. El escenario preventivo real ahora es
    una temperatura DENTRO del rango pero cerca del límite (la salvaguarda
    determinista de clasificar_por_regla garantiza al menos preventivo en esa
    zona). Se siembran dos lecturas normales primero: HU-17 exige historial
    para que la IA pueda calcular tendencia térmica antes de clasificar.
    """
    async with db_session_factory() as session:
        use_case = _construir_use_case(session)
        await use_case.execute(_lectura(-10, temperatura_interna=5.0))
        await session.commit()
    async with db_session_factory() as session:
        use_case = _construir_use_case(session)
        await use_case.execute(_lectura(-5, temperatura_interna=5.0))
        await session.commit()

    async with db_session_factory() as session:
        use_case = _construir_use_case(session)
        # Riesgo preventivo: dentro de rango, a 0.5 °C del límite superior.
        await use_case.execute(_lectura(0, temperatura_interna=7.5))
        await session.commit()

    async with db_session_factory() as session:
        use_case = _construir_use_case(session)
        # Excursión crítica confirmada: fuera de rango.
        await use_case.execute(_lectura(1, temperatura_interna=15.0))
        await session.commit()

    async with db_session_factory() as session:
        alertas = (
            await session.execute(
                select(ThermalAlertModel)
                .where(ThermalAlertModel.device_id == DEVICE_ID)
                .order_by(ThermalAlertModel.created_at)
            )
        ).scalars().all()

    assert len(alertas) == 2, "El escalamiento debe cerrar el episodio anterior y abrir uno nuevo."
    assert alertas[0].nivel_riesgo == "riesgo_preventivo"
    assert alertas[0].episodio_abierto is None
    assert alertas[1].nivel_riesgo == "excursion_critica"
    assert alertas[1].episodio_abierto == 1


async def test_dos_lecturas_concurrentes_del_mismo_episodio_no_duplican_alerta(db_session_factory):
    """Concurrencia: dos lecturas críticas casi simultáneas del mismo
    dispositivo no deben producir dos filas de alerta abiertas. El dispositivo
    se pre-crea (patrón ya usado en test_hash_chain_concurrencia.py) para
    aislar esta prueba de la condición de carrera, ya conocida y fuera de
    alcance de AIV-01..07, del auto-registro de dispositivos nuevos."""
    import asyncio

    from src.infrastructure.database.models import DeviceModel

    async with db_session_factory() as session:
        session.add(DeviceModel(id=DEVICE_ID))
        await session.commit()

    async def registrar(minuto: int) -> None:
        async with db_session_factory() as session:
            use_case = _construir_use_case(session)
            await use_case.execute(_lectura(minuto, temperatura_interna=15.0))
            await session.commit()

    await asyncio.gather(registrar(0), registrar(1))

    async with db_session_factory() as session:
        abiertas = (
            await session.execute(
                select(ThermalAlertModel).where(
                    ThermalAlertModel.device_id == DEVICE_ID, ThermalAlertModel.episodio_abierto == 1
                )
            )
        ).scalars().all()

    assert len(abiertas) == 1, "La concurrencia produjo más de un episodio abierto para el mismo dispositivo/tipo."


# ── AIV-03: guard completo de sensores ───────────────────────────────────
#
# NaN/infinito ya son rechazados en una capa ANTERIOR (LecturaTermica.
# es_lectura_valida(), invocada por RegistrarLecturaTermicaUseCase antes de
# llegar a la clasificación): -55.0 <= NaN <= 125.0 es False en Python, por lo
# que la lectura se rechaza como inválida antes de intentar clasificar. El
# guard de ClasificarRiesgoTermicoUseCase es una segunda capa de defensa en
# profundidad para el caso en que ese valor llegara de otra vía (p. ej. una
# llamada directa al caso de uso sin pasar por es_lectura_valida). Se prueban
# ambas capas por separado.


async def test_temperatura_interna_nan_es_rechazada_como_lectura_invalida(db_session_factory):
    from src.domain.exceptions import LecturaInvalidaError

    async with db_session_factory() as session:
        use_case = _construir_use_case(session)
        # `pytest.raises` en vez de try/except con `assert False`: bajo
        # `python -O` los assert desaparecen y la prueba pasaría aunque la
        # lectura inválida se aceptara sin error.
        with pytest.raises(LecturaInvalidaError):
            await use_case.execute(_lectura(0, temperatura_interna=math.nan))


async def test_temperatura_interna_infinito_es_rechazada_como_lectura_invalida(db_session_factory):
    from src.domain.exceptions import LecturaInvalidaError

    async with db_session_factory() as session:
        use_case = _construir_use_case(session)
        # `pytest.raises` en vez de try/except con `assert False`: bajo
        # `python -O` los assert desaparecen y la prueba pasaría aunque la
        # lectura inválida se aceptara sin error.
        with pytest.raises(LecturaInvalidaError):
            await use_case.execute(_lectura(0, temperatura_interna=math.inf))


def test_guard_de_clasificacion_bloquea_nan_e_infinito_como_defensa_en_profundidad():
    """Prueba unitaria directa del guard (AIV-03), sin pasar por
    es_lectura_valida, para confirmar que la segunda capa de defensa también
    funciona de forma independiente."""
    use_case = ClasificarRiesgoTermicoUseCase(get_random_forest_service())

    resultado_nan = use_case.execute(_lectura(0, temperatura_interna=math.nan), [])
    assert resultado_nan.nivel is None
    assert resultado_nan.confianza is None
    assert resultado_nan.origen == "fallo_sensor"
    assert resultado_nan.estado_inferencia == "omitida"

    resultado_inf = use_case.execute(_lectura(0, temperatura_interna=math.inf), [])
    assert resultado_inf.nivel is None
    assert resultado_inf.origen == "fallo_sensor"


async def test_ambos_sensores_ausentes_no_ejecuta_inferencia(db_session_factory):
    async with db_session_factory() as session:
        use_case = _construir_use_case(session)
        lectura_guardada = await use_case.execute(
            _lectura(0, temperatura_interna=None, temperatura_ambiental=None)
        )
        await session.commit()

    assert lectura_guardada.nivel_riesgo is None
    assert lectura_guardada.confianza_ia is None


async def test_sensor_ambiental_ausente_con_historial_aplica_fallback(db_session_factory):
    """Un sensor válido (interna) y el otro ausente (ambiental) SÍ debe poder
    clasificar si hay un valor ambiental previo en el historial (fallback
    documentado, nunca 0.0). HU-17 exige además 2+ lecturas previas válidas
    para poder calcular tendencia térmica, así que se siembran dos."""
    async with db_session_factory() as session:
        use_case = _construir_use_case(session)
        await use_case.execute(_lectura(0, temperatura_interna=5.0, temperatura_ambiental=21.0))
        await session.commit()

    async with db_session_factory() as session:
        use_case = _construir_use_case(session)
        await use_case.execute(_lectura(1, temperatura_interna=5.0, temperatura_ambiental=21.0))
        await session.commit()

    async with db_session_factory() as session:
        use_case = _construir_use_case(session)
        lectura_guardada = await use_case.execute(
            _lectura(2, temperatura_interna=5.0, temperatura_ambiental=None)
        )
        await session.commit()

    assert lectura_guardada.nivel_riesgo is not None
    assert lectura_guardada.estado_inferencia == "completada"


async def test_temperatura_real_0_grados_es_valida_y_critica(db_session_factory):
    """0.0 °C real (fuera de rango BPA 2-8°C) debe registrar riesgo, no
    tratarse como 'sin dato' (AIV-03) — vía la regla directa HU-21, que es
    independiente de si la IA pudo clasificar. Es la primera lectura del
    dispositivo (sin historial), así que la IA legítimamente queda
    no_clasificable (HU-17: sin datos para tendencia térmica) — lo que NO
    es aceptable es que la excursión deje de detectarse por eso."""
    async with db_session_factory() as session:
        use_case = _construir_use_case(session)
        lectura_guardada = await use_case.execute(
            _lectura(0, temperatura_interna=0.0)
        )
        await session.commit()

    assert lectura_guardada.estado_inferencia == "omitida"
    assert lectura_guardada.nivel_riesgo is None
    assert lectura_guardada.excursion_confirmada is True
    assert lectura_guardada.riesgo_efectivo is not None
    assert lectura_guardada.riesgo_efectivo.value == "excursion_critica"
