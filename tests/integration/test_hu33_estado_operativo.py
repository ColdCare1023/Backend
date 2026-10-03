"""HU-33: estado operativo del nodo (independiente del riesgo térmico)."""

from datetime import datetime, timedelta, timezone

import pytest

from src.domain.entities.lectura_termica import LecturaTermica
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from tests.conftest import auth_header

URL = "/api/dispositivos/estado-operativo"


async def _lectura(factory, device_id: str, hace_s: int, interna: float | None = 5.0, seq: int = 1):
    ahora = datetime.now(timezone.utc)
    async with factory() as session:
        await SQLAlchemyDeviceRepository(session).obtener_o_crear(device_id)
        await SQLAlchemyLecturaRepository(session).agregar(LecturaTermica(
            device_id=device_id, timestamp=ahora - timedelta(seconds=hace_s),
            temperatura_ambiental=22.0, humedad_ambiental=55.0, temperatura_interna=interna,
            apertura_refrigerador=False, estado_conectividad="online", boot_id=1, seq_no=seq,
            received_at=ahora - timedelta(seconds=hace_s),
        ))
        await session.commit()


@pytest.mark.asyncio
async def test_clasifica_operativo_falla_y_sin_datos(client, db_session_factory, token_farmaceutico):
    await _lectura(db_session_factory, "NODO-OK", hace_s=10)
    await _lectura(db_session_factory, "NODO-FALLA", hace_s=10, interna=None)
    await _lectura(db_session_factory, "NODO-MUDO", hace_s=600)

    r = client.get(URL, params={"todos": "true"}, headers=auth_header(token_farmaceutico))

    assert r.status_code == 200
    estados = {d["id"]: d for d in r.json()}
    assert estados["NODO-OK"]["estado_operativo"] == "OPERATIVO"
    assert estados["NODO-FALLA"]["estado_operativo"] == "FALLA_SENSOR"
    assert {s["modelo"]: s["estado"] for s in estados["NODO-FALLA"]["sensores"]}["DS18B20"] == "error"
    assert estados["NODO-MUDO"]["estado_operativo"] == "SIN_DATOS"
    assert estados["NODO-MUDO"]["incidencias_operativas"][0]["tipo"] == "sin_datos"


@pytest.mark.asyncio
async def test_por_defecto_solo_devuelve_nodos_con_incidencias(client, db_session_factory, token_tecnico):
    await _lectura(db_session_factory, "NODO-OK", hace_s=10)
    await _lectura(db_session_factory, "NODO-MUDO", hace_s=600)

    r = client.get(URL, headers=auth_header(token_tecnico))

    assert [d["id"] for d in r.json()] == ["NODO-MUDO"]


def test_exige_autenticacion(client):
    assert client.get(URL).status_code == 401
