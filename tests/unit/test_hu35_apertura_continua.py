"""HU-35: la respuesta distingue la apertura en curso (`door_open_streak_s`
del nodo) de los segundos abiertos dentro de la ventana de muestreo, y
publica el umbral con el que el dashboard la destaca como prolongada."""

from datetime import datetime, timezone

from src.domain.entities.lectura_termica import LecturaTermica
from src.interface.api.mappers import lectura_to_response


def _lectura(apertura: bool | None, payload: dict | None) -> LecturaTermica:
    return LecturaTermica(
        device_id="ESP32_HU35",
        timestamp=datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc),
        temperatura_ambiental=22.0,
        humedad_ambiental=55.0,
        temperatura_interna=5.0,
        apertura_refrigerador=apertura,
        estado_conectividad="online",
        payload=payload,
    )


def test_la_apertura_en_curso_se_expone_aparte_de_la_ventana():
    respuesta = lectura_to_response(
        _lectura(True, {"duracion_apertura_segundos": 10, "door_open_streak_s": 135})
    )
    assert respuesta.duracion_apertura_segundos == 10
    assert respuesta.apertura_continua_segundos == 135
    assert respuesta.umbral_puerta_abierta_segundos == 60


def test_puerta_ya_cerrada_no_tiene_apertura_en_curso():
    respuesta = lectura_to_response(
        _lectura(True, {"duracion_apertura_segundos": 4, "door_open_streak_s": None})
    )
    assert respuesta.apertura_refrigerador is True  # hubo apertura en la ventana
    assert respuesta.apertura_continua_segundos is None


def test_sin_mc38_no_se_publica_umbral_de_puerta():
    respuesta = lectura_to_response(_lectura(None, None))
    assert respuesta.apertura_continua_segundos is None
    assert respuesta.umbral_puerta_abierta_segundos is None
