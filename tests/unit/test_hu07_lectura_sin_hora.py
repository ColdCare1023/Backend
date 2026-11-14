"""HU-07 (H2): una lectura de un arranque sin hora sincronizada que aún no se
reancló se marca «sin hora»: su `timestamp` es la hora de llegada al backend."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.domain.entities.lectura_termica import ORIGEN_MANUAL, LecturaTermica
from src.interface.api.mappers import lectura_to_response

LLEGADA = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)


def _lectura(**cambios) -> LecturaTermica:
    base = {
        "device_id": "ESP32_FARMACIA_01", "timestamp": LLEGADA, "temperatura_ambiental": 21.0,
        "humedad_ambiental": 55.0, "temperatura_interna": 4.5, "apertura_refrigerador": False,
        "estado_conectividad": "online", "boot_id": 7, "seq_no": 1, "received_at": LLEGADA,
    }
    return LecturaTermica(**{**base, **cambios})


@pytest.mark.parametrize(
    ("cambios", "sin_hora", "origen_hora"),
    [
        ({"time_quality": "synced"}, False, "nodo"),
        ({"time_quality": "backfilled"}, False, "nodo"),
        ({"time_quality": None}, False, "nodo"),
        ({"time_quality": "unsynced"}, True, "recepcion"),
        ({"time_quality": "unsynced", "timestamp_estimado": LLEGADA - timedelta(hours=3)}, False, "reconstruida"),
    ],
)
def test_origen_de_la_hora_de_cada_lectura(cambios, sin_hora, origen_hora):
    respuesta = lectura_to_response(_lectura(**cambios))
    assert (respuesta.sin_hora, respuesta.origen_hora) == (sin_hora, origen_hora)
    assert respuesta.recibida_en == LLEGADA


def test_lectura_manual_declara_su_hora():
    respuesta = lectura_to_response(_lectura(
        boot_id=None, seq_no=None, origen=ORIGEN_MANUAL, registrado_por=uuid4(), reading_id="manual-x",
        received_at=None,
    ))
    assert (respuesta.sin_hora, respuesta.origen_hora, respuesta.recibida_en) == (False, "manual", None)


def test_los_campos_nuevos_son_aditivos():
    """Compatibilidad: los campos que ya consumía el frontend no cambian."""
    datos = lectura_to_response(_lectura(time_quality="unsynced")).model_dump(mode="json")
    assert datos["timestamp"].startswith("2026-11-14T10:00:00")
    assert datos["time_quality"] == "unsynced" and datos["timestamp_estimado"] is None
    assert {"sin_hora", "origen_hora", "recibida_en"} <= datos.keys()
