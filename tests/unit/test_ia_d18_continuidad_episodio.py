"""IA-D18 (re-verificación) en `GenerarAlertaUseCase`.

1. Un episodio crítico ATENDIDO que sigue abierto solo absorbe la excursión
   siguiente si esta continúa la anterior (`continua_excursion`). Si entre
   medio hubo una lectura sin temperatura válida o un hueco largo, se cierra
   (`sin_continuidad_tras_atencion`) y se abre una alerta PENDIENTE nueva con
   su aviso. Los episodios PENDIENTE o RECONOCIDA siguen absorbiéndola.
2. Un episodio PREVENTIVO solo cuenta la ventana de normalización con lecturas
   que el tablero muestra como Normal (`normal_confirmado`); una lectura en
   rango sin clase del modelo reinicia la ventana. El crítico se sigue cerrando
   con la primera lectura válida en rango."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.application.use_cases.generar_alerta import (
    CIERRE_NORMALIZACION,
    CIERRE_RECUPERACION,
    CIERRE_SIN_CONTINUIDAD,
    TRANSICION_ACTUALIZACION,
    TRANSICION_CREACION,
    GenerarAlertaUseCase,
)
from src.domain.entities.alerta_termica import AlertaTermica
from src.domain.value_objects.estado_alerta import EstadoAlerta
from src.domain.value_objects.nivel_riesgo import NivelRiesgo

DEVICE_ID = "ESP32-D18-UNIT"
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
    alerta = await repo.agregar(
        AlertaTermica(
            reading_id=uuid4(), device_id=DEVICE_ID, nivel_riesgo=nivel, mensaje="m",
            iniciada_en=BASE, ultima_actualizacion=BASE, temperatura_pico=8.5,
        )
    )
    alerta.estado = estado
    return alerta


async def _lectura(caso, nivel, minuto: float, **kwargs):
    return await caso.execute(
        reading_id=uuid4(), device_id=DEVICE_ID, nivel_riesgo=nivel,
        timestamp=BASE + timedelta(minutes=minuto), temperatura_interna=9.5 if nivel == C else 5.0, **kwargs,
    )


# ── 1. Continuidad de la excursión tras la atención ─────────────────────────


async def test_atendida_sin_continuidad_se_cierra_y_la_excursion_es_una_alerta_nueva():
    repo = _Repo()
    atendida = await _abierto(repo, C, EstadoAlerta.ATENDIDA)
    caso = GenerarAlertaUseCase(repo)

    nueva = await _lectura(caso, C, 7, continua_excursion=False)

    assert nueva.id != atendida.id
    assert nueva.estado == EstadoAlerta.PENDIENTE
    assert nueva.episodio_abierto is True
    assert caso.transicion == TRANSICION_CREACION
    assert (atendida.episodio_abierto, atendida.estado) == (False, EstadoAlerta.ATENDIDA)
    assert atendida.cerrada_en == BASE + timedelta(minutes=7)
    assert caso.cierres == [atendida]
    assert caso.motivos_cierre[atendida.id] == CIERRE_SIN_CONTINUIDAD == "sin_continuidad_tras_atencion"
    [aviso] = caso.avisos_pendientes
    assert (aviso.alerta_id, aviso.nivel, aviso.temperatura) == (nueva.id, C, 9.5)


async def test_atendida_con_la_excursion_continua_la_absorbe_como_hasta_ahora():
    repo = _Repo()
    atendida = await _abierto(repo, C, EstadoAlerta.ATENDIDA)
    caso = GenerarAlertaUseCase(repo)

    misma = await _lectura(caso, C, 0.5)  # continua_excursion=True por defecto

    assert misma is atendida
    assert misma.episodio_abierto is True
    assert misma.temperatura_pico == 9.5
    assert caso.transicion == TRANSICION_ACTUALIZACION
    assert caso.cierres == [] and caso.avisos_pendientes == []
    assert len(repo.filas) == 1


@pytest.mark.parametrize("estado", [EstadoAlerta.PENDIENTE, EstadoAlerta.RECONOCIDA])
async def test_sin_atender_la_alerta_sigue_absorbiendo_la_excursion(estado):
    """Una alerta todavía sin atender ya está a la vista y avisada: no hace falta otra."""
    repo = _Repo()
    abierta = await _abierto(repo, C, estado)
    caso = GenerarAlertaUseCase(repo)

    misma = await _lectura(caso, C, 30, continua_excursion=False)

    assert misma is abierta
    assert misma.estado == estado
    assert caso.cierres == [] and caso.avisos_pendientes == []


async def test_la_continuidad_no_afecta_a_otros_niveles():
    """Preventiva ATENDIDA + excursión: escala como siempre (cambio de nivel)."""
    repo = _Repo()
    preventiva = await _abierto(repo, P, EstadoAlerta.ATENDIDA)
    caso = GenerarAlertaUseCase(repo)

    critica = await _lectura(caso, C, 3, continua_excursion=False)

    assert critica.id != preventiva.id
    assert caso.motivos_cierre[preventiva.id] == "cambio_de_nivel"
    assert [a.alerta_id for a in caso.avisos_pendientes] == [critica.id]


# ── 2. La ventana preventiva solo cuenta lecturas Normal confirmadas ───────


async def test_lectura_en_rango_sin_clase_no_inicia_la_ventana_preventiva():
    repo = _Repo()
    preventiva = await _abierto(repo, P)
    caso = GenerarAlertaUseCase(repo, minutos_normales_preventiva=10)

    for minuto in range(1, 13):
        assert await _lectura(caso, N, minuto, normal_confirmado=False) is None

    assert preventiva.episodio_abierto is True
    assert preventiva.normalizacion_desde is None
    assert caso.cierres == []
    assert repo.actualizaciones == 0  # nada que guardar


async def test_lectura_sin_clase_reinicia_la_ventana_preventiva_en_curso():
    repo = _Repo()
    preventiva = await _abierto(repo, P)
    caso = GenerarAlertaUseCase(repo, minutos_normales_preventiva=10)

    await _lectura(caso, N, 1)
    assert preventiva.normalizacion_desde == BASE + timedelta(minutes=1)
    await _lectura(caso, N, 2, normal_confirmado=False)
    assert preventiva.normalizacion_desde is None
    await _lectura(caso, N, 11)  # 10 min desde el minuto 1, pero no seguidos

    assert preventiva.episodio_abierto is True
    assert preventiva.normalizacion_desde == BASE + timedelta(minutes=11)
    await _lectura(caso, N, 21)
    assert preventiva.episodio_abierto is False
    assert caso.motivos_cierre[preventiva.id] == CIERRE_NORMALIZACION


async def test_con_ventana_cero_la_preventiva_tampoco_se_cierra_sin_clase():
    repo = _Repo()
    preventiva = await _abierto(repo, P)
    caso = GenerarAlertaUseCase(repo, minutos_normales_preventiva=0)

    await _lectura(caso, N, 1, normal_confirmado=False)
    assert preventiva.episodio_abierto is True

    await _lectura(caso, N, 2)
    assert preventiva.episodio_abierto is False
    assert caso.motivos_cierre[preventiva.id] == CIERRE_RECUPERACION


async def test_la_critica_se_cierra_con_la_primera_lectura_en_rango_aunque_no_haya_clase():
    repo = _Repo()
    critica = await _abierto(repo, C, EstadoAlerta.ATENDIDA)
    caso = GenerarAlertaUseCase(repo)

    await _lectura(caso, N, 2, normal_confirmado=False)

    assert critica.episodio_abierto is False
    assert caso.motivos_cierre[critica.id] == CIERRE_RECUPERACION
