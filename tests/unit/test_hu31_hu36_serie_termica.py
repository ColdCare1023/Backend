"""HU-31 / HU-36: elección del intervalo de agregación, rango por defecto y
clasificación de brechas de la serie térmica (sin base de datos)."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.application.use_cases.consultar_serie_termica import (
    MAX_BUCKETS_AUTO,
    RangoSerieInvalidoError,
    clasificar_brechas,
    elegir_bucket,
    normalizar_rango,
)
from src.domain.entities.serie_termica import BrechaSerie
from src.domain.repositories.i_estado_nodo_repository import EpisodioBufferSaturado

AHORA = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("periodo", "esperado"),
    [
        (timedelta(hours=1), 60),
        (timedelta(hours=24), 60),
        (timedelta(days=7), 600),
        (timedelta(days=30), 1800),
        (timedelta(days=90), 7200),
        (timedelta(days=366), 21600),
    ],
)
def test_bucket_automatico_deja_la_curva_acotada(periodo, esperado):
    bucket = elegir_bucket(AHORA - periodo, AHORA)
    assert bucket == esperado
    assert periodo.total_seconds() / bucket <= MAX_BUCKETS_AUTO


def test_bucket_explicito_valido_se_respeta():
    assert elegir_bucket(AHORA - timedelta(hours=24), AHORA, 30) == 30


@pytest.mark.parametrize("bucket", [5, 86401])
def test_bucket_fuera_de_limites_se_rechaza(bucket):
    with pytest.raises(RangoSerieInvalidoError, match="entre 10 y 86400"):
        elegir_bucket(AHORA - timedelta(hours=1), AHORA, bucket)


def test_bucket_que_devolveria_la_serie_cruda_se_rechaza():
    # 24 h a 10 s son 8640 puntos: eso no es una serie agregada.
    with pytest.raises(RangoSerieInvalidoError, match="más de 5000 puntos"):
        elegir_bucket(AHORA - timedelta(hours=24), AHORA, 10)


def test_sin_rango_son_las_ultimas_24_horas():
    """HU-31 esc. 4: ventana por defecto de 24 h."""
    assert normalizar_rango(None, None, AHORA) == (AHORA - timedelta(hours=24), AHORA)


def test_solo_hasta_toma_las_24_horas_anteriores():
    hasta = AHORA - timedelta(days=3)
    assert normalizar_rango(None, hasta, AHORA) == (hasta - timedelta(hours=24), hasta)


def test_hora_sin_zona_se_interpreta_en_utc():
    desde, hasta = normalizar_rango(datetime(2026, 11, 13, 12, 0), datetime(2026, 11, 14, 12, 0), AHORA)
    assert desde.tzinfo is timezone.utc and hasta == AHORA


def test_hora_con_otra_zona_se_convierte_a_utc():
    lima = timezone(timedelta(hours=-5))
    desde, _ = normalizar_rango(datetime(2026, 11, 14, 2, 0, tzinfo=lima), AHORA, AHORA)
    assert desde == datetime(2026, 11, 14, 7, 0, tzinfo=timezone.utc) and desde.tzinfo is timezone.utc


def test_rango_invertido_se_rechaza_en_el_backend():
    """HU-36 esc. 3: una llamada directa a la API también se valida."""
    with pytest.raises(RangoSerieInvalidoError, match="desde no puede ser posterior a hasta"):
        normalizar_rango(AHORA, AHORA - timedelta(minutes=1), AHORA)


def test_periodo_mayor_a_un_anio_se_rechaza():
    with pytest.raises(RangoSerieInvalidoError, match="366 días"):
        normalizar_rango(AHORA - timedelta(days=367), AHORA, AHORA)


def _episodio(desde: datetime | None, hasta: datetime | None, perdidas: int) -> EpisodioBufferSaturado:
    return EpisodioBufferSaturado(
        id=uuid4(), device_id="N1", causa="buffer_lleno", perdidas=perdidas, abierto=False,
        registrado_en=AHORA, actualizado_en=AHORA, desde=desde, hasta=hasta,
    )


def test_brecha_que_coincide_con_buffer_saturado_lleva_su_causa_y_perdidas():
    """HU-48 esc. 2: la brecha temporal se muestra con su causa."""
    inicio, fin = AHORA - timedelta(hours=2), AHORA - timedelta(hours=1)
    brechas = clasificar_brechas(
        [(inicio, fin, False), (AHORA - timedelta(minutes=10), AHORA, True)],
        [_episodio(inicio + timedelta(minutes=5), fin - timedelta(minutes=5), 117)],
    )
    assert brechas[0] == BrechaSerie(desde=inicio, hasta=fin, causa="buffer_agotado", perdidas=117, abierta=False)
    assert brechas[0].duracion_segundos == 3600
    assert (brechas[1].causa, brechas[1].perdidas, brechas[1].abierta) == ("sin_datos", None, True)


def test_episodio_sin_hora_no_se_atribuye_a_ninguna_brecha():
    brechas = clasificar_brechas([(AHORA - timedelta(hours=1), AHORA, False)], [_episodio(None, None, 5)])
    assert brechas[0].causa == "sin_datos"
