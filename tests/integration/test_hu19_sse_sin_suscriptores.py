"""HU-19 esc. 2 (sin clientes conectados): «cuando se intenta emitir el
evento, el despacho se omite para ahorrar recursos, pero el dato no se pierde
ya que queda disponible en PostgreSQL para consultas históricas».

Se ejercita el manejador MQTT real (`interface/main.py`) con el broadcaster
real y ningún suscriptor: el último cliente ya cerró su pestaña. La lectura y
la alerta que abre se consultan después por la API del historial.
"""

import json
from datetime import datetime, timedelta, timezone

from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.interface import main
from src.interface.api.sse_broadcaster import SSEBroadcaster
from tests.conftest import auth_header

DEVICE = "FARM-HU19-SIN-SSE"


class _Mensaje:
    def __init__(self, payload: dict) -> None:
        self.topic = f"farmacias/{DEVICE}/lecturas"
        self.payload = json.dumps(payload).encode("utf-8")


def _lectura(instante: datetime, temperatura: float, seq_no: int) -> dict:
    return {
        "device_id": DEVICE, "timestamp": instante.isoformat(), "schema_version": 1,
        "reading_id": f"hu19-sin-sse-{seq_no}", "boot_id": 1, "seq_no": seq_no,
        "temperatura_interna": temperatura, "temperatura_ambiental": 22.0, "humedad_ambiental": 55.0,
        "apertura_refrigerador": False, "estado_conectividad": "online", "time_quality": "synced",
    }


async def test_publicar_sin_suscriptores_no_falla_y_la_lectura_persiste(
    client, token_tecnico, db_session_factory, monkeypatch
):
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    async with db_session_factory() as session:
        await SQLAlchemyDeviceRepository(session).obtener_o_crear(DEVICE)
        await session.commit()

    broadcaster = SSEBroadcaster()
    broadcaster.unsubscribe(broadcaster.subscribe())  # el último cliente cerró la pestaña
    assert broadcaster._subscribers == set()
    # Sin suscriptores no hay a quién despachar: publicar no falla ni crea colas.
    await broadcaster.publicar({"device_id": DEVICE}, "lectura")
    assert broadcaster._subscribers == set()

    # Una lectura en rango y una excursión (que además publica `alerta`).
    inicio = datetime.now(tz=timezone.utc).replace(microsecond=0) - timedelta(minutes=5)
    for seq_no, temperatura in enumerate((5.0, 9.0), start=1):
        await main._procesar_lectura_mqtt(
            _Mensaje(_lectura(inicio + timedelta(minutes=seq_no), temperatura, seq_no)), broadcaster
        )
    assert broadcaster._subscribers == set()

    # El dato no se pierde: queda en la base y la consulta histórica lo devuelve.
    respuesta = client.get("/api/lecturas", params={"device_id": DEVICE}, headers=auth_header(token_tecnico))
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.headers["X-Total-Count"] == "2"
    lecturas = {lectura["temperatura_interna"]: lectura for lectura in respuesta.json()}
    assert set(lecturas) == {5.0, 9.0}
    assert lecturas[5.0]["riesgo_efectivo"] == "normal"
    assert lecturas[9.0]["riesgo_efectivo"] == "excursion_critica"

    alertas = client.get("/api/alertas", params={"device_id": DEVICE}, headers=auth_header(token_tecnico))
    assert alertas.status_code == 200, alertas.text
    assert [alerta["nivel_riesgo"] for alerta in alertas.json()] == ["excursion_critica"]
