"""BE-001: la vía REST registra lecturas MANUALES, separadas de la telemetría.

Una persona autenticada no puede producir datos atribuidos a un dispositivo ni
ocupar por adelantado la identidad de una lectura real del nodo."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from src.infrastructure.database.models import AuditLogModel, ThermalReadingModel, TraceabilityRecordModel
from src.interface import main
from tests.conftest import auth_header

DEVICE_ID = "FARM-BE001"


def _manual(**overrides) -> dict:
    base = {
        "device_id": DEVICE_ID,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "temperatura_ambiental": 21.0,
        "humedad_ambiental": 50.0,
        "temperatura_interna": 5.0,
        "apertura_refrigerador": False,
        "estado_conectividad": "online",
    }
    base.update(overrides)
    return base


class _Mensaje:
    def __init__(self, payload: dict) -> None:
        self.topic = f"farmacias/{DEVICE_ID}/lecturas"
        self.payload = json.dumps(payload).encode()


class _Espia:
    def __init__(self) -> None:
        self.acuses: list[dict] = []

    async def publish(self, topic, payload=None, qos=0, **_kwargs):
        self.acuses.append(json.loads(payload))


class _Broadcaster:
    async def publicar(self, *_args, **_kwargs):
        return None


def test_lectura_rest_queda_marcada_como_manual_con_autor(client, token_tecnico):
    respuesta = client.post("/api/lecturas", json=_manual(), headers=auth_header(token_tecnico))

    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["origen"] == "manual"
    assert cuerpo["registrado_por"] is not None
    assert cuerpo["reading_id"].startswith("manual-")


@pytest.mark.parametrize("campo,valor", [("boot_id", 1), ("seq_no", 5), ("reading_id", "x"), ("schema_version", 1)])
def test_lectura_rest_no_acepta_identidad_del_dispositivo(client, token_tecnico, campo, valor):
    respuesta = client.post("/api/lecturas", json=_manual(**{campo: valor}), headers=auth_header(token_tecnico))
    assert respuesta.status_code == 422


def test_lectura_manual_tiene_ventana_temporal_corta(client, token_tecnico):
    antigua = (datetime.now(tz=timezone.utc) - timedelta(hours=30)).isoformat()
    respuesta = client.post("/api/lecturas", json=_manual(timestamp=antigua), headers=auth_header(token_tecnico))
    assert respuesta.status_code == 422


async def test_lectura_manual_queda_auditada_y_encadenada_con_su_autor(client, token_tecnico, db_session_factory):
    creada = client.post("/api/lecturas", json=_manual(), headers=auth_header(token_tecnico)).json()

    async with db_session_factory() as session:
        auditoria = (
            await session.execute(select(AuditLogModel).where(AuditLogModel.accion == "LECTURA_MANUAL_REGISTRADA"))
        ).scalar_one()
        eslabon = (
            await session.execute(
                select(TraceabilityRecordModel).where(TraceabilityRecordModel.tipo_evento == "LECTURA_TERMICA")
            )
        ).scalar_one()

    assert str(auditoria.usuario_id) == creada["registrado_por"]
    assert eslabon.payload["origen"] == "manual"
    assert eslabon.payload["registrado_por"] == creada["registrado_por"]
    assert str(eslabon.usuario_id) == creada["registrado_por"]


async def test_lectura_manual_no_desplaza_la_telemetria_del_nodo(
    client, token_tecnico, db_session_factory, monkeypatch
):
    """La lectura manual con el mismo instante que una lectura futura del nodo
    no la convierte en "duplicado": las dos se conservan."""
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    instante = datetime.now(tz=timezone.utc).replace(microsecond=0) - timedelta(minutes=5)
    assert client.post(
        "/api/lecturas", json=_manual(timestamp=instante.isoformat(), temperatura_interna=4.0),
        headers=auth_header(token_tecnico),
    ).status_code == 201

    espia = _Espia()
    telemetria = {
        "device_id": DEVICE_ID, "timestamp": instante.isoformat(), "schema_version": 1,
        "temperatura_interna": 9.5, "temperatura_ambiental": 22.0, "humedad_ambiental": 50.0,
        "apertura_refrigerador": False, "estado_conectividad": "online",
    }
    await main._procesar_lectura_mqtt(_Mensaje(telemetria), _Broadcaster(), espia)

    assert espia.acuses[0]["estado"] == "commit_confirmado"
    async with db_session_factory() as session:
        origenes = sorted(
            (await session.execute(
                select(ThermalReadingModel.origen).where(ThermalReadingModel.device_id == DEVICE_ID)
            )).scalars().all()
        )
    assert origenes == ["dispositivo", "manual"]
