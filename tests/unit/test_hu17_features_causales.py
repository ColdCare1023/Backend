"""HU-17 esc. 1: las features de historial del clasificador solo usan
lecturas anteriores a t (sin fuga de información futura) y se calculan igual
en entrenamiento y en servicio, sin depender de la cadencia del nodo."""

from datetime import datetime, timedelta, timezone

import pytest

from src.application.use_cases.clasificar_riesgo_termico import (
    LIMITE_HISTORIAL_IA,
    PASO_REJILLA_MIN,
    VENTANA_HISTORIAL_MIN,
    ClasificarRiesgoTermicoUseCase,
    historial_en_rejilla,
)
from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.ai import train_model_v3
from src.infrastructure.ai.random_forest_service import ESTADO_COMPLETADA, ORIGEN_MODELO, ResultadoInferencia

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def _lectura(segundos: float, temperatura: float | None = 5.0) -> LecturaTermica:
    return LecturaTermica(
        device_id="HU17", timestamp=T0 + timedelta(seconds=segundos), temperatura_ambiental=21.0,
        humedad_ambiental=55.0, temperatura_interna=temperatura, apertura_refrigerador=False,
        estado_conectividad="online",
    )


class _Captura:
    """Doble del servicio de IA: guarda las features que recibe."""

    metadata = {"model_version": "doble"}

    def __init__(self) -> None:
        self.features = []

    def inferir(self, features) -> ResultadoInferencia:
        self.features.append(features)
        return ResultadoInferencia(NivelRiesgo.NORMAL, 0.9, ORIGEN_MODELO, ESTADO_COMPLETADA)


def _features_de_servicio(lectura, historial):
    captura = _Captura()
    ClasificarRiesgoTermicoUseCase(captura, "activo").execute(lectura, historial)
    assert captura.features, "el modelo no se llamó"
    return captura.features[-1]


# ── Sin fuga ────────────────────────────────────────────────────────────────


def test_la_rejilla_descarta_t_y_todo_lo_posterior():
    lectura = _lectura(0)
    historial = [_lectura(-120), _lectura(-60), _lectura(0, 9.9), _lectura(60, 9.9), _lectura(600, 9.9)]

    elegidas = historial_en_rejilla(lectura, historial)

    assert [e.timestamp for e in elegidas] == [T0 - timedelta(minutes=2), T0 - timedelta(minutes=1)]


def test_lecturas_futuras_no_cambian_las_features():
    pasado = [_lectura(-60 * m, 5.0 + 0.1 * m) for m in range(10, 0, -1)]
    futuro = [_lectura(60 * m, 9.5) for m in range(1, 20)]  # reenvío del buffer: llegan después
    lectura = _lectura(0, 5.0)

    sin_futuro = _features_de_servicio(lectura, pasado)
    con_futuro = _features_de_servicio(lectura, pasado + futuro)

    assert con_futuro == sin_futuro
    assert con_futuro.frecuencia_desviaciones == 0.0
    assert con_futuro.duracion_fuera_rango == 0.0


def test_solo_historial_futuro_es_historial_insuficiente():
    captura = _Captura()
    resultado = ClasificarRiesgoTermicoUseCase(captura, "activo").execute(
        _lectura(0), [_lectura(60 * m) for m in range(1, 10)]
    )

    assert resultado.nivel is None
    assert resultado.motivo_no_inferencia == "historial_insuficiente_para_tendencia"
    assert captura.features == []


def test_el_respaldo_ambiental_tampoco_mira_el_futuro():
    lectura = _lectura(0)
    lectura.temperatura_ambiental = None
    pasado = [_lectura(-60 * m) for m in (3, 2, 1)]
    futuro = _lectura(60)
    futuro.temperatura_ambiental = 30.0

    features = _features_de_servicio(lectura, [*pasado, futuro])

    assert features.temperatura_ambiental == 21.0


# ── Independencia de la cadencia ────────────────────────────────────────────


def _rampa(paso_s: int, minutos: int, pendiente_c_min: float) -> list[LecturaTermica]:
    n = minutos * 60 // paso_s
    return [_lectura(-paso_s * i, 5.0 - pendiente_c_min * paso_s * i / 60) for i in range(n, 0, -1)]


@pytest.mark.parametrize("paso_s", [10, 30, 60])
def test_pendiente_en_grados_por_minuto_a_cualquier_cadencia(paso_s):
    features = _features_de_servicio(_lectura(0, 5.0), _rampa(paso_s, 20, 0.05))

    assert features.tendencia_termica == pytest.approx(0.05, abs=1e-9)


def test_misma_serie_a_10_s_y_a_1_min_da_el_mismo_vector():
    """La rejilla toma la lectura más cercana a cada minuto: a 10 s, la de
    t-1 min, t-2 min... exactamente las que habría a 1 min."""
    valores = {m: (8.4 if 5 <= m <= 8 else 6.0 + 0.02 * m) for m in range(1, 31)}
    cada_minuto = [_lectura(-60 * m, valores[m]) for m in range(30, 0, -1)]
    cada_10_s = [
        _lectura(-s, valores[min(30, max(1, round(s / 60)))]) for s in range(30 * 60, 0, -10)
    ]

    assert _features_de_servicio(_lectura(0, 6.0), cada_10_s) == _features_de_servicio(
        _lectura(0, 6.0), cada_minuto
    )


def test_huecos_no_distorsionan_la_pendiente():
    serie = [_lectura(-60 * m, 5.0 - 0.1 * m) for m in range(12, 0, -1) if m not in (4, 5, 9)]

    assert _features_de_servicio(_lectura(0, 5.0), serie).tendencia_termica == pytest.approx(0.1)


def test_lo_anterior_a_la_ventana_no_cuenta():
    viejas = [_lectura(-60 * m, 12.0) for m in range(VENTANA_HISTORIAL_MIN + 1, VENTANA_HISTORIAL_MIN + 30)]
    recientes = [_lectura(-60 * m, 5.0) for m in (3, 2, 1)]

    features = _features_de_servicio(_lectura(0, 5.0), viejas + recientes)

    assert features.frecuencia_desviaciones == 0.0


def test_a_10_s_dos_lecturas_de_los_ultimos_20_s_no_son_una_ventana():
    resultado = ClasificarRiesgoTermicoUseCase(_Captura(), "activo").execute(
        _lectura(0), [_lectura(-20), _lectura(-10)]
    )
    assert resultado.motivo_no_inferencia == "historial_insuficiente_para_tendencia"


def test_el_limite_del_repositorio_cubre_la_ventana_a_10_s():
    assert LIMITE_HISTORIAL_IA >= VENTANA_HISTORIAL_MIN * 60 / 10
    assert PASO_REJILLA_MIN == 1.0  # train_model_v3 genera un tick por minuto


# ── Paridad entrenamiento / servicio ────────────────────────────────────────


def test_la_ventana_no_recorta_lo_que_vio_el_entrenamiento():
    # El episodio más largo de train_model_v3 tiene TICKS_MAX ticks de 1 min:
    # su última lectura ve TICKS_MAX - 1 minutos de historial.
    assert train_model_v3.TICKS_MAX - 1 <= VENTANA_HISTORIAL_MIN


def test_entrenamiento_y_servicio_calculan_el_mismo_vector():
    """train_model_v3 construye cada fila con el historial del episodio hasta
    ese tick. El servicio recibe además lecturas posteriores (reenvío del
    buffer) y debe llegar exactamente al mismo vector."""
    temperaturas = [5.0, 5.2, 5.1, 5.6, 6.0, 6.5, 7.1, 7.6, 8.3, 8.9, 9.4, 8.7, 7.9, 7.2, 6.6, 6.1]
    episodio = [_lectura(60 * i, t) for i, t in enumerate(temperaturas)]
    for i, lectura in enumerate(episodio):
        if i < 2 or lectura.es_excursion_confirmada():
            continue  # sin ventana o fuera de rango: el modelo no se ejecuta
        en_entrenamiento = train_model_v3._extractor._construir_features(lectura, episodio[:i])
        en_servicio = _features_de_servicio(lectura, episodio)  # incluye el futuro
        assert en_servicio == en_entrenamiento, f"tick {i}"


def test_el_entrenamiento_sigue_siendo_reproducible():
    import numpy as np

    x1, y1, _ = train_model_v3.generar_dataset_v3(5, np.random.default_rng(7))
    x2, y2, _ = train_model_v3.generar_dataset_v3(5, np.random.default_rng(7))
    assert (x1 == x2).all() and (y1 == y2).all()
