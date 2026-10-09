"""HU-23 en el pipeline: el aviso se emite al ABRIR un episodio crítico, no
en cada lectura del mismo episodio.

Sin esta distinción, una excursión de una hora con muestreo cada 30 segundos
generaría 120 avisos por el mismo evento."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.application.use_cases.generar_alerta import GenerarAlertaUseCase
from src.domain.entities.alerta_termica import AlertaTermica
from src.domain.repositories.i_alerta_repository import IAlertaRepository
from src.domain.value_objects.estado_alerta import EstadoAlerta
from src.domain.value_objects.nivel_riesgo import NivelRiesgo

DEVICE_ID = "ESP32-NOTIF-01"
BASE = datetime.now(tz=timezone.utc) - timedelta(hours=1)


class _RepoEnMemoria(IAlertaRepository):
    def __init__(self) -> None:
        self.filas: list[AlertaTermica] = []

    async def agregar(self, alerta: AlertaTermica) -> AlertaTermica:
        alerta.id = uuid4()
        self.filas.append(alerta)
        return alerta

    async def actualizar(self, alerta: AlertaTermica) -> AlertaTermica:
        return alerta

    async def obtener_episodio_abierto(self, device_id: str) -> AlertaTermica | None:
        abiertos = [a for a in self.filas if a.device_id == device_id and a.episodio_abierto]
        return abiertos[-1] if abiertos else None

    async def obtener_ultimo_cerrado(self, device_id: str, nivel_riesgo) -> AlertaTermica | None:
        cerrados = [
            a
            for a in self.filas
            if a.device_id == device_id and not a.episodio_abierto and a.nivel_riesgo == nivel_riesgo
        ]
        return cerrados[-1] if cerrados else None

    async def listar(self, device_id=None, revisada=None, estado=None, limite=100, offset=0):
        return list(self.filas)

    async def obtener_por_id(self, alerta_id):
        return next((a for a in self.filas if a.id == alerta_id), None)

    async def marcar_revisada(self, alerta_id, usuario_id):
        return None

    async def marcar_atendida_si_no_atendida(self, alerta_id, timestamp) -> bool:
        alerta = next((a for a in self.filas if a.id == alerta_id), None)
        if alerta is None or alerta.estado == EstadoAlerta.ATENDIDA:
            return False
        alerta.estado = EstadoAlerta.ATENDIDA
        alerta.atendida_en = timestamp
        return True


def _avisos(use_case: GenerarAlertaUseCase) -> list[tuple[str, float | None]]:
    """HU-53: el caso de uso ya no envía; deja avisos para después del COMMIT."""
    return [(a.device_id, a.temperatura) for a in use_case.avisos_pendientes]


@pytest.mark.asyncio
async def test_apertura_de_episodio_critico_dispara_un_aviso():
    use_case = GenerarAlertaUseCase(_RepoEnMemoria())

    await use_case.execute(
        reading_id=uuid4(),
        device_id=DEVICE_ID,
        nivel_riesgo=NivelRiesgo.EXCURSION_CRITICA,
        timestamp=BASE,
        temperatura_interna=14.5,
    )

    assert _avisos(use_case) == [(DEVICE_ID, 14.5)]


@pytest.mark.asyncio
async def test_lecturas_sucesivas_del_mismo_episodio_no_reavisan():
    repo = _RepoEnMemoria()
    use_case = GenerarAlertaUseCase(repo)

    for minuto in range(6):
        await use_case.execute(
            reading_id=uuid4(),
            device_id=DEVICE_ID,
            nivel_riesgo=NivelRiesgo.EXCURSION_CRITICA,
            timestamp=BASE + timedelta(minutes=minuto),
            temperatura_interna=14.5,
        )

    assert len(_avisos(use_case)) == 1
    assert len(repo.filas) == 1


@pytest.mark.asyncio
async def test_riesgo_preventivo_no_dispara_aviso_externo():
    """Solo la excursión crítica justifica interrumpir al responsable fuera de
    la aplicación; el riesgo preventivo se ve en el dashboard."""
    use_case = GenerarAlertaUseCase(_RepoEnMemoria())

    await use_case.execute(
        reading_id=uuid4(),
        device_id=DEVICE_ID,
        nivel_riesgo=NivelRiesgo.RIESGO_PREVENTIVO,
        timestamp=BASE,
        temperatura_interna=8.4,
    )

    assert _avisos(use_case) == []


@pytest.mark.asyncio
async def test_escalar_de_preventivo_a_critico_si_avisa():
    use_case = GenerarAlertaUseCase(_RepoEnMemoria())

    await use_case.execute(
        reading_id=uuid4(), device_id=DEVICE_ID, nivel_riesgo=NivelRiesgo.RIESGO_PREVENTIVO,
        timestamp=BASE, temperatura_interna=8.4,
    )
    await use_case.execute(
        reading_id=uuid4(), device_id=DEVICE_ID, nivel_riesgo=NivelRiesgo.EXCURSION_CRITICA,
        timestamp=BASE + timedelta(minutes=1), temperatura_interna=14.9,
    )

    assert _avisos(use_case) == [(DEVICE_ID, 14.9)]


@pytest.mark.asyncio
async def test_la_alerta_se_genera_aunque_nadie_despache_el_aviso():
    """Sin despachador (pruebas, REST sin lifespan) la alerta se genera igual."""
    repo = _RepoEnMemoria()
    use_case = GenerarAlertaUseCase(repo)

    alerta = await use_case.execute(
        reading_id=uuid4(),
        device_id=DEVICE_ID,
        nivel_riesgo=NivelRiesgo.EXCURSION_CRITICA,
        timestamp=BASE,
        temperatura_interna=14.5,
    )

    assert alerta is not None
    assert len(repo.filas) == 1


@pytest.mark.asyncio
async def test_reapertura_dentro_de_la_ventana_conserva_el_id_de_la_alerta():
    """HU-53/P3: la reapertura deja un aviso con el MISMO id de alerta; el
    despachador lo descarta porque ese episodio ya fue notificado."""
    repo = _RepoEnMemoria()
    use_case = GenerarAlertaUseCase(repo)
    await use_case.execute(
        reading_id=uuid4(), device_id=DEVICE_ID, nivel_riesgo=NivelRiesgo.EXCURSION_CRITICA,
        timestamp=BASE, temperatura_interna=12.0,
    )
    await use_case.execute(
        reading_id=uuid4(), device_id=DEVICE_ID, nivel_riesgo=NivelRiesgo.NORMAL,
        timestamp=BASE + timedelta(minutes=2), temperatura_interna=5.0,
    )
    await use_case.execute(
        reading_id=uuid4(), device_id=DEVICE_ID, nivel_riesgo=NivelRiesgo.EXCURSION_CRITICA,
        timestamp=BASE + timedelta(minutes=5), temperatura_interna=12.5,
    )
    ids = {a.alerta_id for a in use_case.avisos_pendientes}
    assert len(repo.filas) == 1
    assert ids == {repo.filas[0].id}


@pytest.mark.asyncio
async def test_lectura_recibida_tarde_marca_el_aviso_como_diferido():
    use_case = GenerarAlertaUseCase(_RepoEnMemoria())
    await use_case.execute(
        reading_id=uuid4(), device_id=DEVICE_ID, nivel_riesgo=NivelRiesgo.EXCURSION_CRITICA,
        timestamp=BASE, temperatura_interna=12.0,
        received_at=BASE + timedelta(minutes=20), time_quality="synced",
    )
    assert use_case.avisos_pendientes[0].diferida
