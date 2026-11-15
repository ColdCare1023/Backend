"""HU-37: alteración dentro del segmento y constancia de la verificación.

Escenario 1: ancla en el registro inmediatamente anterior al periodo y
recorre todo el segmento en orden de chain_seq. Escenario 2: la lectura
alterada se señala "corrupta" con su ID y timestamp y la verificación sigue
con el resto. Escenario 3: evento VERIFICACION_INTEGRIDAD con dispositivo,
periodo, resultado global y usuario solicitante, bajo el hash."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from src.infrastructure.config import get_settings
from src.infrastructure.database.models import TraceabilityRecordModel
from tests.conftest import auth_header

DEVICE = "ESP32-HU37-SEG"
BASE = datetime.now(tz=timezone.utc).replace(microsecond=0) - timedelta(hours=2)


def _ingestar(client, token, minuto: int) -> None:
    respuesta = client.post(
        "/api/lecturas",
        json={
            "device_id": DEVICE,
            "timestamp": (BASE + timedelta(minutes=minuto)).isoformat(),
            "temperatura_interna": 5.0,
            "temperatura_ambiental": 21.0,
            "humedad_ambiental": 55.0,
            "apertura_refrigerador": False,
            "estado_conectividad": "online",
        },
        headers=auth_header(token),
    )
    assert respuesta.status_code == 201, respuesta.text


async def _lecturas(fabrica) -> list[TraceabilityRecordModel]:
    async with fabrica() as session:
        return list(
            (
                await session.execute(
                    select(TraceabilityRecordModel)
                    .where(TraceabilityRecordModel.chain_id == DEVICE)
                    .order_by(TraceabilityRecordModel.chain_seq)
                )
            ).scalars().all()
        )


async def _alterar(fabrica, registro_id) -> None:
    async with fabrica() as session:
        fila = await session.get(TraceabilityRecordModel, registro_id)
        fila.payload = {**fila.payload, "temperatura_interna": 99.0}
        await session.commit()


def _segmento(client, token, desde: datetime, hasta: datetime) -> dict:
    respuesta = client.get(
        "/api/trazabilidad/verificar-dispositivo",
        params={"device_id": DEVICE, "desde": desde.isoformat(), "hasta": hasta.isoformat()},
        headers=auth_header(token),
    )
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


async def test_lectura_alterada_dentro_del_rango_es_corrupta_y_el_resto_se_evalua(
    client, token_farmaceutico, db_session_factory
):
    for minuto in (0, 10, 20, 30, 40):
        _ingestar(client, token_farmaceutico, minuto)
    filas = await _lecturas(db_session_factory)
    fuera, dentro = filas[0], filas[2]
    await _alterar(db_session_factory, fuera.id)
    await _alterar(db_session_factory, dentro.id)

    cuerpo = _segmento(client, token_farmaceutico, BASE + timedelta(minutes=15), BASE + timedelta(minutes=45))

    # Escenario 1: el segmento son los minutos 20, 30 y 40, sin omitir ninguno.
    assert cuerpo["total_bloques_verificados"] == 3
    ids = [r["id"] for r in cuerpo["registros_del_dispositivo"]]
    assert ids == [str(f.id) for f in filas[2:5]]
    assert str(fuera.id) not in ids  # lo alterado fuera del periodo no cuenta
    # Escenario 2: "Corrupta" con su ID y timestamp, y la evaluación continúa.
    assert cuerpo["estado"] == "corrupta"
    assert cuerpo["integra"] is False
    assert cuerpo["primer_registro_inconsistente"] == str(dentro.id)
    assert cuerpo["total_corruptos"] == 1
    assert [r["estado"] for r in cuerpo["registros_del_dispositivo"]] == ["corrupto", "integro", "integro"]
    alterado = cuerpo["registros_del_dispositivo"][0]
    assert alterado["tipo_discrepancia"] == "payload_alterado"
    assert alterado["timestamp"] and alterado["integro"] is False
    # El ancla es el eslabón inmediatamente anterior al periodo (minuto 10).
    assert cuerpo["hash_ancla"] == filas[1].hash_actual


async def test_alterar_el_ancla_no_invalida_el_segmento(client, token_farmaceutico, db_session_factory):
    """Se compara contra el hash ALMACENADO del eslabón anterior al periodo."""
    for minuto in (0, 10, 20):
        _ingestar(client, token_farmaceutico, minuto)
    filas = await _lecturas(db_session_factory)
    await _alterar(db_session_factory, filas[1].id)

    cuerpo = _segmento(client, token_farmaceutico, BASE + timedelta(minutes=15), BASE + timedelta(minutes=25))

    assert cuerpo["total_bloques_verificados"] == 1
    assert cuerpo["estado"] == "integra"


async def test_el_evento_de_verificacion_lleva_periodo_resultado_y_usuario(
    client, token_farmaceutico, db_session_factory
):
    for minuto in (0, 10):
        _ingestar(client, token_farmaceutico, minuto)
    filas = await _lecturas(db_session_factory)
    await _alterar(db_session_factory, filas[1].id)
    desde, hasta = BASE - timedelta(minutes=1), BASE + timedelta(minutes=11)

    cuerpo = _segmento(client, token_farmaceutico, desde, hasta)

    eventos = [f for f in await _lecturas(db_session_factory) if f.tipo_evento == "VERIFICACION_INTEGRIDAD"]
    assert len(eventos) == 1
    evento = eventos[0]
    assert evento.chain_id == DEVICE and evento.device_id == DEVICE
    assert evento.previous_hash == filas[-1].hash_actual  # encadenado al último eslabón
    payload = evento.payload
    assert payload["device_id"] == DEVICE
    assert datetime.fromisoformat(payload["desde"]) == desde
    assert datetime.fromisoformat(payload["hasta"]) == hasta
    assert payload["integra"] is False
    assert payload["resultado"] == "corrupta"
    assert payload["registros_corruptos"] == 1
    assert payload["primer_registro_inconsistente"] == cuerpo["primer_registro_inconsistente"]
    # El solicitante queda bajo el hash, no solo en la columna usuario_id.
    assert payload["usuario_id"] == str(evento.usuario_id)
    assert evento.usuario_id is not None


async def test_el_aviso_de_corrupcion_anclado_por_el_sistema_no_es_falso_positivo(
    client, token_farmaceutico, db_session_factory
):
    """Tras GET /verificar (HU-26) se encadena CORRUPCION_CADENA_DETECTADA
    anclado al último eslabón íntegro, fuera de la secuencia normal."""
    for minuto in (0, 10, 20):
        _ingestar(client, token_farmaceutico, minuto)
    filas = await _lecturas(db_session_factory)
    await _alterar(db_session_factory, filas[1].id)
    verificar = client.get(
        "/api/trazabilidad/verificar", params={"chain_id": DEVICE}, headers=auth_header(token_farmaceutico)
    )
    assert verificar.json()["estado"] == "ruptura_detectada"

    cuerpo = _segmento(
        client, token_farmaceutico, BASE - timedelta(minutes=1), datetime.now(tz=timezone.utc) + timedelta(minutes=1)
    )

    por_tipo = {r["tipo_evento"]: r for r in cuerpo["registros_del_dispositivo"]}
    assert por_tipo["CORRUPCION_CADENA_DETECTADA"]["estado"] == "integro"
    corruptos = [r["id"] for r in cuerpo["registros_del_dispositivo"] if r["estado"] == "corrupto"]
    assert corruptos == [str(filas[1].id)]


async def test_sin_clave_el_segmento_v3_no_concluye(client, token_farmaceutico, monkeypatch):
    monkeypatch.setattr(get_settings(), "trazabilidad_hmac_key", "k" * 40)
    for minuto in (0, 10):
        _ingestar(client, token_farmaceutico, minuto)
    monkeypatch.setattr(get_settings(), "trazabilidad_hmac_key", None)

    cuerpo = _segmento(client, token_farmaceutico, BASE - timedelta(minutes=1), BASE + timedelta(minutes=11))

    assert cuerpo["estado"] == "no_concluida"
    assert cuerpo["integra"] is False
    assert cuerpo["total_corruptos"] == 0
    assert cuerpo["total_no_verificables"] == 2
    assert {r["estado"] for r in cuerpo["registros_del_dispositivo"]} == {"no_verificable"}
    assert cuerpo["versiones_hash"] == [3]


@pytest.mark.parametrize("clave", [None, "k" * 40])
async def test_segmento_integro_en_v2_y_v3(client, token_farmaceutico, monkeypatch, clave):
    monkeypatch.setattr(get_settings(), "trazabilidad_hmac_key", clave)
    for minuto in (0, 10):
        _ingestar(client, token_farmaceutico, minuto)

    cuerpo = _segmento(client, token_farmaceutico, BASE - timedelta(minutes=1), BASE + timedelta(minutes=11))

    assert cuerpo["estado"] == "integra"
    assert cuerpo["versiones_hash"] == [3 if clave else 2]
    assert all(r["estado"] == "integro" for r in cuerpo["registros_del_dispositivo"])
