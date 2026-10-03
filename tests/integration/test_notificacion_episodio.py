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


class _NotificadorEspia:
    def __init__(self) -> None:
        self.avisos: list[tuple[str, float | None]] = []

    async def notificar_excursion_critica(
        self, device_id, temperatura, timestamp, email_destino=None, telefono_destino=None
    ):
        self.avisos.append((device_id, temperatura))


@pytest.mark.asyncio
async def test_apertura_de_episodio_critico_dispara_un_aviso():
    notificador = _NotificadorEspia()
    use_case = GenerarAlertaUseCase(_RepoEnMemoria(), notificador)

    await use_case.execute(
        reading_id=uuid4(),
        device_id=DEVICE_ID,
        nivel_riesgo=NivelRiesgo.EXCURSION_CRITICA,
        timestamp=BASE,
        temperatura_interna=14.5,
    )

    assert notificador.avisos == [(DEVICE_ID, 14.5)]


@pytest.mark.asyncio
async def test_lecturas_sucesivas_del_mismo_episodio_no_reavisan():
    notificador = _NotificadorEspia()
    repo = _RepoEnMemoria()
    use_case = GenerarAlertaUseCase(repo, notificador)

    for minuto in range(6):
        await use_case.execute(
            reading_id=uuid4(),
            device_id=DEVICE_ID,
            nivel_riesgo=NivelRiesgo.EXCURSION_CRITICA,
            timestamp=BASE + timedelta(minutes=minuto),
            temperatura_interna=14.5,
        )

    assert len(notificador.avisos) == 1
    assert len(repo.filas) == 1


@pytest.mark.asyncio
async def test_riesgo_preventivo_no_dispara_aviso_externo():
    """Solo la excursión crítica justifica interrumpir al responsable fuera de
    la aplicación; el riesgo preventivo se ve en el dashboard."""
    notificador = _NotificadorEspia()
    use_case = GenerarAlertaUseCase(_RepoEnMemoria(), notificador)

    await use_case.execute(
        reading_id=uuid4(),
        device_id=DEVICE_ID,
        nivel_riesgo=NivelRiesgo.RIESGO_PREVENTIVO,
        timestamp=BASE,
        temperatura_interna=8.4,
    )

    assert notificador.avisos == []


@pytest.mark.asyncio
async def test_escalar_de_preventivo_a_critico_si_avisa():
    notificador = _NotificadorEspia()
    use_case = GenerarAlertaUseCase(_RepoEnMemoria(), notificador)

    await use_case.execute(
        reading_id=uuid4(), device_id=DEVICE_ID, nivel_riesgo=NivelRiesgo.RIESGO_PREVENTIVO,
        timestamp=BASE, temperatura_interna=8.4,
    )
    await use_case.execute(
        reading_id=uuid4(), device_id=DEVICE_ID, nivel_riesgo=NivelRiesgo.EXCURSION_CRITICA,
        timestamp=BASE + timedelta(minutes=1), temperatura_interna=14.9,
    )

    assert notificador.avisos == [(DEVICE_ID, 14.9)]


@pytest.mark.asyncio
async def test_sin_notificador_el_pipeline_funciona_igual():
    """El servicio es opcional: sin él, la alerta se genera exactamente igual."""
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
