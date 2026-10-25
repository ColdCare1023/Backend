"""HU-26: la verificación informa todas las rupturas sin ocultar lo posterior
(escenario 2), puede reintentarse sin duplicar avisos (escenario 3), declara
su alcance (escenario 4) y, tras aislar la ruptura (HU-47), vuelve a dar la
cadena por verificable desde el bloque génesis del aislamiento."""

from datetime import datetime, timezone

from sqlalchemy import select

from src.infrastructure.database.models import TraceabilityRecordModel
from tests.conftest import auth_header

DEVICE = "esp32-hu26-01"


def _ingestar(client, token, n: int = 1) -> None:
    for _ in range(n):
        respuesta = client.post(
            "/api/lecturas",
            json={
                "device_id": DEVICE,
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "temperatura_ambiental": 22.0,
                "humedad_ambiental": 40.0,
                "temperatura_interna": 4.0,
                "apertura_refrigerador": False,
                "estado_conectividad": "online",
            },
            headers=auth_header(token),
        )
        assert respuesta.status_code == 201


async def _corromper(db_session_factory, posicion: int) -> str:
    """Altera el payload del eslabón `posicion` de la cadena del dispositivo."""
    async with db_session_factory() as session:
        registros = (
            await session.execute(
                select(TraceabilityRecordModel)
                .where(TraceabilityRecordModel.chain_id == DEVICE)
                .order_by(TraceabilityRecordModel.chain_seq)
            )
        ).scalars().all()
        registro = registros[posicion]
        registro.payload = {**registro.payload, "temperatura_interna": 999.0}
        await session.commit()
        return str(registro.id)


def _verificar(client, token) -> dict:
    respuesta = client.get("/api/trazabilidad/verificar", params={"chain_id": DEVICE}, headers=auth_header(token))
    assert respuesta.status_code == 200
    return respuesta.json()


def _eventos(client, token, tipo: str) -> list:
    return client.get("/api/trazabilidad", params={"tipo_evento": tipo}, headers=auth_header(token)).json()


async def test_ruptura_puntual_no_invalida_lo_posterior(client, token_tecnico, db_session_factory):
    _ingestar(client, token_tecnico, 4)
    corrupto = await _corromper(db_session_factory, 1)

    resultado = _verificar(client, token_tecnico)

    assert resultado["estado"] == "ruptura_detectada"
    assert resultado["total_rupturas"] == 1
    assert resultado["rupturas"][0]["id"] == corrupto
    assert resultado["rupturas"][0]["tipo_discrepancia"] == "payload_alterado"
    # Los posteriores siguen enlazados a lo almacenado: se informan, no se ocultan.
    assert resultado["registros_posteriores_afectados"] >= 2
    assert resultado["registros_posteriores_consistentes"] == resultado["registros_posteriores_afectados"]


async def test_reintentar_la_verificacion_no_duplica_el_aviso(client, token_tecnico, db_session_factory):
    _ingestar(client, token_tecnico, 3)
    await _corromper(db_session_factory, 0)

    for _ in range(3):
        assert _verificar(client, token_tecnico)["estado"] == "ruptura_detectada"

    assert len(_eventos(client, token_tecnico, "CORRUPCION_CADENA_DETECTADA")) == 1


async def test_tras_aislar_la_cadena_vuelve_a_ser_verificable(
    client, token_admin, token_tecnico, db_session_factory
):
    _ingestar(client, token_tecnico, 3)
    corrupto = await _corromper(db_session_factory, 1)
    _verificar(client, token_tecnico)
    ruta = f"/api/trazabilidad/corrupcion/{corrupto}/aislar"
    assert client.post(ruta, headers=auth_header(token_admin)).status_code == 204

    _ingestar(client, token_tecnico, 2)
    resultado = _verificar(client, token_tecnico)

    assert resultado["estado"] == "ruptura_aislada"
    assert resultado["integra"] is False  # la alteración histórica sigue a la vista
    assert resultado["integra_desde"] is not None
    assert resultado["rupturas"][0]["aislada"] is True
    assert len(_eventos(client, token_tecnico, "CORRUPCION_CADENA_DETECTADA")) == 1
    estado = client.get("/api/trazabilidad/estado", headers=auth_header(token_tecnico)).json()
    assert estado["cadena_comprometida"] is False


async def test_una_ruptura_nueva_tras_aislar_se_puede_aislar(
    client, token_admin, token_tecnico, db_session_factory
):
    _ingestar(client, token_tecnico, 2)
    primera = await _corromper(db_session_factory, 0)
    _verificar(client, token_tecnico)
    assert client.post(f"/api/trazabilidad/corrupcion/{primera}/aislar", headers=auth_header(token_admin)).status_code == 204

    _ingestar(client, token_tecnico, 3)
    async with db_session_factory() as session:
        ultimo = (
            await session.execute(
                select(TraceabilityRecordModel)
                .where(TraceabilityRecordModel.chain_id == DEVICE)
                .order_by(TraceabilityRecordModel.chain_seq.desc())
            )
        ).scalars().first()
        segunda = str(ultimo.id)
        ultimo.payload = {**ultimo.payload, "alterado": True}
        await session.commit()

    resultado = _verificar(client, token_tecnico)
    assert resultado["estado"] == "ruptura_detectada"
    assert resultado["detalle_inconsistencia"]["id"] == segunda
    assert client.post(f"/api/trazabilidad/corrupcion/{segunda}/aislar", headers=auth_header(token_admin)).status_code == 204
    assert _verificar(client, token_tecnico)["estado"] == "ruptura_aislada"


def test_el_resultado_declara_su_alcance(client, token_tecnico):
    _ingestar(client, token_tecnico)
    assert "DIGEMID" in _verificar(client, token_tecnico)["alcance"]

    segmento = client.get(
        "/api/trazabilidad/verificar-dispositivo",
        params={"device_id": DEVICE, "desde": "2020-01-01T00:00:00Z", "hasta": "2100-01-01T00:00:00Z"},
        headers=auth_header(token_tecnico),
    )
    assert segmento.status_code == 200
    assert "DIGEMID" in segmento.json()["alcance"]
