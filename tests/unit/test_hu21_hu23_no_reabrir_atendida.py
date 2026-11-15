"""HU-21 esc. 3 / HU-23 esc. 3: la ventana de normalización reabre un episodio
recién cerrado (histéresis, HU-20), salvo que ya esté ATENDIDO: entonces la
nueva excursión es otra alerta PENDIENTE, con su propio aviso."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.application.use_cases.generar_alerta import (
    TRANSICION_CREACION,
    TRANSICION_REAPERTURA,
    GenerarAlertaUseCase,
)
from src.domain.entities.alerta_termica import AlertaTermica
from src.domain.repositories.i_alerta_repository import IAlertaRepository
from src.domain.value_objects.estado_alerta import EstadoAlerta
from src.domain.value_objects.nivel_riesgo import NivelRiesgo

DEVICE_ID = "ESP32-REAB-UNIT"
BASE = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


class _Repo(IAlertaRepository):
    def __init__(self) -> None:
        self.filas: list[AlertaTermica] = []

    async def agregar(self, alerta):
        alerta.id = uuid4()
        self.filas.append(alerta)
        return alerta

    async def obtener_por_id(self, alerta_id):
        return next((a for a in self.filas if a.id == alerta_id), None)

    async def listar(self, device_id=None, revisada=None, estado=None, desde=None, hasta=None, limite=100, offset=0):
        return list(self.filas)

    async def actualizar(self, alerta):
        return alerta

    async def marcar_atendida_si_no_atendida(self, alerta_id, timestamp):
        return False

    async def obtener_episodio_abierto(self, device_id):
        abiertos = [a for a in self.filas if a.device_id == device_id and a.episodio_abierto]
        return abiertos[-1] if abiertos else None

    async def obtener_ultimo_cerrado(self, device_id, nivel_riesgo):
        cerrados = [a for a in self.filas if not a.episodio_abierto and a.nivel_riesgo == nivel_riesgo]
        return cerrados[-1] if cerrados else None


async def _episodio_cerrado(repo: _Repo, nivel: NivelRiesgo, estado: EstadoAlerta) -> AlertaTermica:
    alerta = await repo.agregar(
        AlertaTermica(reading_id=uuid4(), device_id=DEVICE_ID, nivel_riesgo=nivel, mensaje="m", iniciada_en=BASE)
    )
    alerta.estado = estado
    alerta.cerrar(BASE + timedelta(minutes=2))
    return alerta


async def _nueva_lectura(caso: GenerarAlertaUseCase, nivel: NivelRiesgo) -> AlertaTermica:
    # 5 minutos después del cierre: dentro de la ventana por defecto (15 min).
    return await caso.execute(
        reading_id=uuid4(), device_id=DEVICE_ID, nivel_riesgo=nivel,
        timestamp=BASE + timedelta(minutes=7), temperatura_interna=10.4,
    )


@pytest.mark.parametrize("nivel", [NivelRiesgo.EXCURSION_CRITICA, NivelRiesgo.RIESGO_PREVENTIVO])
async def test_un_episodio_atendido_no_se_reabre_y_la_nueva_alerta_se_avisa(nivel):
    repo = _Repo()
    atendida = await _episodio_cerrado(repo, nivel, EstadoAlerta.ATENDIDA)
    caso = GenerarAlertaUseCase(repo)

    nueva = await _nueva_lectura(caso, nivel)

    assert nueva.id != atendida.id
    assert nueva.estado == EstadoAlerta.PENDIENTE
    assert caso.transicion == TRANSICION_CREACION
    assert atendida.episodio_abierto is False
    assert atendida.estado == EstadoAlerta.ATENDIDA
    assert [a.alerta_id for a in caso.avisos_pendientes] == [nueva.id]


@pytest.mark.parametrize("estado", [EstadoAlerta.PENDIENTE, EstadoAlerta.RECONOCIDA])
async def test_un_episodio_sin_atender_conserva_la_histeresis(estado):
    repo = _Repo()
    cerrado = await _episodio_cerrado(repo, NivelRiesgo.EXCURSION_CRITICA, estado)
    caso = GenerarAlertaUseCase(repo)

    reabierta = await _nueva_lectura(caso, NivelRiesgo.EXCURSION_CRITICA)

    assert reabierta.id == cerrado.id
    assert reabierta.episodio_abierto is True
    assert reabierta.estado == estado
    assert caso.transicion == TRANSICION_REAPERTURA
    assert len(repo.filas) == 1
