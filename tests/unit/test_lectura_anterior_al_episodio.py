"""Una lectura capturada ANTES que la última del episodio abierto (carga
manual retroactiva de hasta 24 h, lectura del buffer que llega después de una
en vivo) queda como evidencia y no cambia el ciclo del episodio.

Antes, una lectura en rango con hora de captura anterior cerraba el episodio
crítico como «recuperación» con `cerrada_en` anterior a `iniciada_en`, y la
siguiente lectura en vivo, todavía fuera de rango, abría otra alerta PENDIENTE
con otro aviso para la misma excursión continua."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.application.use_cases.generar_alerta import (
    TRANSICION_ACTUALIZACION,
    TRANSICION_CREACION,
    GenerarAlertaUseCase,
)
from src.domain.entities.alerta_termica import AlertaTermica
from src.domain.value_objects.estado_alerta import EstadoAlerta
from src.domain.value_objects.nivel_riesgo import NivelRiesgo

DEVICE_ID = "ESP32-RETRO-UNIT"
BASE = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
P, N, C = NivelRiesgo.RIESGO_PREVENTIVO, NivelRiesgo.NORMAL, NivelRiesgo.EXCURSION_CRITICA


class _Repo:
    def __init__(self) -> None:
        self.filas: list[AlertaTermica] = []
        self.actualizaciones = 0

    async def agregar(self, alerta):
        alerta.id = uuid4()
        self.filas.append(alerta)
        return alerta

    async def actualizar(self, alerta):
        self.actualizaciones += 1
        return alerta

    async def obtener_episodio_abierto(self, device_id):
        return next((a for a in self.filas if a.device_id == device_id and a.episodio_abierto), None)

    async def obtener_ultimo_cerrado(self, device_id, nivel_riesgo):
        cerrados = [a for a in self.filas if a.nivel_riesgo == nivel_riesgo and not a.episodio_abierto]
        return max(cerrados, key=lambda a: a.cerrada_en, default=None)


async def _abierto(repo: _Repo, nivel: NivelRiesgo, estado: EstadoAlerta = EstadoAlerta.PENDIENTE) -> AlertaTermica:
    lectura = uuid4()
    alerta = await repo.agregar(
        AlertaTermica(
            reading_id=lectura, device_id=DEVICE_ID, nivel_riesgo=nivel, mensaje="m",
            lectura_inicial_id=lectura, lectura_mas_reciente_id=lectura,
            iniciada_en=BASE, ultima_actualizacion=BASE, temperatura_pico=9.0 if nivel == C else 7.6,
        )
    )
    alerta.estado = estado
    return alerta


async def _lectura(caso, nivel, minuto: float, temperatura: float | None = None, **kwargs):
    if temperatura is None:
        temperatura = {C: 9.0, P: 7.6, N: 5.0}.get(nivel, 5.0)
    return await caso.execute(
        reading_id=uuid4(), device_id=DEVICE_ID, nivel_riesgo=nivel,
        timestamp=BASE + timedelta(minutes=minuto), temperatura_interna=temperatura, **kwargs,
    )


@pytest.mark.parametrize("estado", list(EstadoAlerta))
@pytest.mark.parametrize("nivel", [N, None])
@pytest.mark.parametrize("normal_confirmado", [True, False])
async def test_en_rango_anterior_no_cierra_el_critico(estado, nivel, normal_confirmado):
    repo = _Repo()
    critica = await _abierto(repo, C, estado)
    caso = GenerarAlertaUseCase(repo)

    assert await _lectura(caso, nivel, -180, 5.0, normal_confirmado=normal_confirmado) is None

    assert critica.episodio_abierto is True and critica.cerrada_en is None
    assert caso.cierres == [] and caso.motivos_cierre == {} and caso.avisos_pendientes == []
    assert caso.transicion is None
    assert repo.actualizaciones == 0


async def test_la_lectura_en_vivo_siguiente_sigue_en_la_misma_alerta_sin_otro_aviso():
    """La secuencia de la sonda: 9 °C en vivo, 5 °C cargada 3 h atrás, 9 °C en vivo."""
    repo = _Repo()
    critica = await _abierto(repo, C)
    caso = GenerarAlertaUseCase(repo)

    await _lectura(caso, N, -180)
    siguiente = await _lectura(caso, C, 0.5)

    assert siguiente is critica
    assert caso.transicion == TRANSICION_ACTUALIZACION
    assert len(repo.filas) == 1
    assert caso.avisos_pendientes == []
    assert critica.ultima_actualizacion == BASE + timedelta(minutes=0.5)


async def test_en_rango_posterior_a_la_ultima_lectura_sigue_cerrando_el_critico():
    repo = _Repo()
    critica = await _abierto(repo, C)
    caso = GenerarAlertaUseCase(repo)

    await _lectura(caso, N, 1)

    assert critica.episodio_abierto is False
    assert critica.cerrada_en == BASE + timedelta(minutes=1)


async def test_critica_anterior_es_evidencia_del_mismo_episodio_atendido():
    """Ni el cierre sin continuidad (IA-D18) ni una alerta nueva: la lectura es
    anterior a la que el episodio ya cuenta. Solo puede subir el pico."""
    repo = _Repo()
    atendida = await _abierto(repo, C, EstadoAlerta.ATENDIDA)
    lectura_mas_reciente = atendida.lectura_mas_reciente_id
    caso = GenerarAlertaUseCase(repo)

    misma = await _lectura(caso, C, -30, 11.0, continua_excursion=False)

    assert misma is atendida
    assert atendida.episodio_abierto is True and atendida.estado == EstadoAlerta.ATENDIDA
    assert atendida.temperatura_pico == 11.0
    # La hora y la lectura más reciente no retroceden.
    assert atendida.ultima_actualizacion == BASE
    assert atendida.lectura_mas_reciente_id == lectura_mas_reciente
    assert caso.cierres == [] and caso.avisos_pendientes == []
    assert caso.transicion == TRANSICION_ACTUALIZACION
    assert len(repo.filas) == 1


async def test_critica_anterior_no_mueve_la_hora_y_la_en_rango_siguiente_no_reabre_la_falla():
    """Si la hora del episodio retrocediera, una lectura en rango posterior a la
    retroactiva pero anterior a la última real volvería a cerrarlo."""
    repo = _Repo()
    critica = await _abierto(repo, C)
    caso = GenerarAlertaUseCase(repo)

    await _lectura(caso, C, -180)
    await _lectura(caso, N, -120)

    assert critica.episodio_abierto is True


async def test_preventiva_anterior_no_baja_el_critico():
    repo = _Repo()
    critica = await _abierto(repo, C)
    caso = GenerarAlertaUseCase(repo)

    assert await _lectura(caso, P, -10) is None

    assert critica.episodio_abierto is True
    assert caso.cierres == [] and caso.avisos_pendientes == []
    assert len(repo.filas) == 1


async def test_critica_anterior_con_un_preventivo_abierto_escala_y_avisa():
    """La excursión ocurrió: con el preventivo abierto se escala como siempre."""
    repo = _Repo()
    preventiva = await _abierto(repo, P)
    caso = GenerarAlertaUseCase(repo)

    critica = await _lectura(caso, C, -10)

    assert critica.id != preventiva.id and critica.nivel_riesgo == C
    assert caso.transicion == TRANSICION_CREACION
    assert [a.alerta_id for a in caso.avisos_pendientes] == [critica.id]


async def test_lecturas_anteriores_no_tocan_la_ventana_de_normalizacion_preventiva():
    repo = _Repo()
    preventiva = await _abierto(repo, P)
    caso = GenerarAlertaUseCase(repo, minutos_normales_preventiva=10)

    await _lectura(caso, N, 1)
    assert preventiva.normalizacion_desde == BASE + timedelta(minutes=1)
    # Una preventiva y una Normal sin clase, ambas capturadas antes de la última preventiva.
    await _lectura(caso, P, -5)
    await _lectura(caso, N, -3, normal_confirmado=False)
    assert preventiva.normalizacion_desde == BASE + timedelta(minutes=1)
    assert preventiva.ultima_actualizacion == BASE

    await _lectura(caso, N, 11)
    assert preventiva.episodio_abierto is False
