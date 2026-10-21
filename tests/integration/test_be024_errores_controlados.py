"""BE-024: condiciones previsibles que terminaban en 500.

- `POST /api/ia/clasificar` sin modelo cargado: la regla de salvaguarda decide
  y `confianza` es null (antes: `round(None)` → TypeError).
- Deduplicación heredada por (device_id, timestamp): lecturas del firmware
  nuevo pueden repetir marca de tiempo; la consulta ya no lanza
  MultipleResultsFound.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.application.use_cases.registrar_lectura_termica import RegistrarLecturaTermicaUseCase
from src.domain.entities.lectura_termica import LecturaTermica
from src.infrastructure.ai.random_forest_service import get_random_forest_service
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.trazabilidad_repository import SQLAlchemyTrazabilidadRepository
from tests.conftest import auth_header

DEVICE = "D-BE024"
INSTANTE = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).replace(microsecond=0)

VECTOR = {
    "temperatura_ambiental": 21.0,
    "humedad_ambiental": 55.0,
    "temperatura_interna": 5.0,
    "diferencia_sensores": 16.0,
    "duracion_fuera_rango": 0.0,
    "frecuencia_desviaciones": 0.0,
    "tendencia_termica": 0.0,
    "apertura_refrigerador": False,
    "hora_evento": 12,
    "estado_conectividad_online": True,
}


# ── Clasificación sin modelo ─────────────────────────────────────────────


def test_clasificar_sin_modelo_devuelve_confianza_nula(client, token_farmaceutico, monkeypatch):
    servicio = get_random_forest_service()
    monkeypatch.setattr(type(servicio), "_cargar_modelo", lambda _self: None)

    respuesta = client.post("/api/ia/clasificar", json=VECTOR, headers=auth_header(token_farmaceutico))

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["confianza"] is None
    assert cuerpo["nivel_riesgo"] is not None
    assert cuerpo["estado_inferencia"] == "modelo_no_disponible"


def test_clasificar_con_modelo_conserva_la_confianza(client, token_farmaceutico):
    respuesta = client.post("/api/ia/clasificar", json=VECTOR, headers=auth_header(token_farmaceutico))

    assert respuesta.status_code == 200
    if respuesta.json()["estado_inferencia"] == "completada":
        assert 0.0 <= respuesta.json()["confianza"] <= 1.0


# ── Deduplicación heredada ───────────────────────────────────────────────


def _lectura(**identidad) -> LecturaTermica:
    return LecturaTermica(
        device_id=DEVICE,
        timestamp=INSTANTE,
        temperatura_ambiental=20.0,
        humedad_ambiental=50.0,
        temperatura_interna=5.0,
        apertura_refrigerador=False,
        estado_conectividad="online",
        **identidad,
    )


def _caso(session) -> RegistrarLecturaTermicaUseCase:
    return RegistrarLecturaTermicaUseCase(
        SQLAlchemyLecturaRepository(session),
        SQLAlchemyAlertaRepository(session),
        SQLAlchemyTrazabilidadRepository(session),
        ClasificarRiesgoTermicoUseCase(get_random_forest_service()),
        device_repository=SQLAlchemyDeviceRepository(session),
    )


@pytest.mark.asyncio
async def test_lecturas_nuevas_con_la_misma_hora_no_rompen_la_deduplicacion_heredada(db_session):
    await SQLAlchemyDeviceRepository(db_session).obtener_o_crear(DEVICE)
    caso = _caso(db_session)
    # Firmware nuevo sin NTP: dos lecturas distintas con la misma marca.
    await caso.execute(_lectura(boot_id=1, seq_no=1, time_quality="unsynced", uptime_ms=1_000))
    await caso.execute(_lectura(boot_id=1, seq_no=2, time_quality="unsynced", uptime_ms=2_000))
    repo = SQLAlchemyLecturaRepository(db_session)

    assert await repo.obtener_por_device_y_timestamp(DEVICE, INSTANTE) is None

    heredada = await caso.execute(_lectura())
    assert heredada.boot_id is None
    reenvio = await caso.execute(_lectura())
    assert reenvio.id == heredada.id
    assert (await repo.obtener_por_device_y_timestamp(DEVICE, INSTANTE)).id == heredada.id
