from datetime import datetime, timezone

from sqlalchemy import select

from src.infrastructure.database.models import TraceabilityRecordModel
from tests.conftest import auth_header


def _ingestar_lectura(client, token, device_id: str):
    return client.post(
        "/api/lecturas",
        json={
            "device_id": device_id,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "temperatura_ambiental": 22.0,
            "humedad_ambiental": 40.0,
            "temperatura_interna": 4.0,
            "apertura_refrigerador": False,
            "estado_conectividad": "online",
        },
        headers=auth_header(token),
    )


async def _corromper_primer_registro(db_session_factory) -> str:
    """Simula un ataque/alteración directa en BD: modifica el payload de un
    registro ya encadenado, de forma que su hash almacenado deje de coincidir
    con el que la cadena recalcula (HU-47 Escenario 1)."""
    async with db_session_factory() as session:
        result = await session.execute(
            select(TraceabilityRecordModel).order_by(TraceabilityRecordModel.created_at.asc()).limit(1)
        )
        registro = result.scalar_one()
        registro_id = str(registro.id)
        registro.payload = {**registro.payload, "temperatura_interna": 999.0}
        await session.commit()
    return registro_id


async def test_verificar_integridad_sin_corrupcion_retorna_integra(client, token_tecnico):
    _ingestar_lectura(client, token_tecnico, "esp32-integro-01")

    response = client.get("/api/trazabilidad/verificar", headers=auth_header(token_tecnico))
    assert response.status_code == 200
    body = response.json()
    assert body["integra"] is True
    assert body["detalle_inconsistencia"] is None


async def test_verificar_detecta_corrupcion_y_notifica(client, token_tecnico, db_session_factory):
    _ingestar_lectura(client, token_tecnico, "esp32-corrupto-01")
    registro_id = await _corromper_primer_registro(db_session_factory)

    response = client.get("/api/trazabilidad/verificar", headers=auth_header(token_tecnico))
    assert response.status_code == 200
    body = response.json()
    assert body["integra"] is False
    assert body["detalle_inconsistencia"]["id"] == registro_id


async def test_verificar_corrupcion_activa_flag_cadena_comprometida(client, token_tecnico, db_session_factory):
    _ingestar_lectura(client, token_tecnico, "esp32-corrupto-02")
    await _corromper_primer_registro(db_session_factory)

    client.get("/api/trazabilidad/verificar", headers=auth_header(token_tecnico))

    estado = client.get("/api/trazabilidad/estado", headers=auth_header(token_tecnico))
    assert estado.status_code == 200
    assert estado.json()["cadena_comprometida"] is True


async def test_verificar_corrupcion_registra_evento_de_emergencia_encadenado(
    client, token_tecnico, db_session_factory
):
    _ingestar_lectura(client, token_tecnico, "esp32-corrupto-03")
    await _corromper_primer_registro(db_session_factory)

    client.get("/api/trazabilidad/verificar", headers=auth_header(token_tecnico))

    trazabilidad = client.get(
        "/api/trazabilidad",
        params={"tipo_evento": "CORRUPCION_CADENA_DETECTADA"},
        headers=auth_header(token_tecnico),
    )
    assert trazabilidad.status_code == 200
    assert len(trazabilidad.json()) == 1


async def test_admin_aisla_registro_corrupto_y_restaura_estado(client, token_admin, token_tecnico, db_session_factory):
    _ingestar_lectura(client, token_tecnico, "esp32-corrupto-04")
    registro_id = await _corromper_primer_registro(db_session_factory)

    client.get("/api/trazabilidad/verificar", headers=auth_header(token_tecnico))
    assert client.get("/api/trazabilidad/estado", headers=auth_header(token_tecnico)).json()[
        "cadena_comprometida"
    ] is True

    aislar = client.post(
        f"/api/trazabilidad/corrupcion/{registro_id}/aislar", headers=auth_header(token_admin)
    )
    assert aislar.status_code == 204

    estado = client.get("/api/trazabilidad/estado", headers=auth_header(token_tecnico))
    assert estado.json()["cadena_comprometida"] is False


async def test_aislamiento_queda_en_la_bitacora_con_su_autor(
    client, token_admin, token_tecnico, db_session_factory
):
    """RF-16: dar por rota la evidencia y arrancar una cadena nueva es la
    intervención manual más sensible del sistema.

    Antes solo dejaba rastro dentro de la propia cadena y sin autor, así que la
    bitácora no podía responder quién puso la evidencia en cuarentena — justo
    la pregunta que haría cualquier auditoría."""
    from src.infrastructure.database.repositories.audit_log_repository import (
        SQLAlchemyAuditLogRepository,
    )

    _ingestar_lectura(client, token_tecnico, "esp32-corrupto-07")
    registro_id = await _corromper_primer_registro(db_session_factory)
    client.get("/api/trazabilidad/verificar", headers=auth_header(token_tecnico))

    assert (
        client.post(
            f"/api/trazabilidad/corrupcion/{registro_id}/aislar",
            headers=auth_header(token_admin),
        ).status_code
        == 204
    )

    async with db_session_factory() as session:
        entradas = await SQLAlchemyAuditLogRepository(session).listar(limite=100)

    aislamientos = [e for e in entradas if e["accion"] == "CADENA_CORRUPCION_AISLADA"]
    assert len(aislamientos) == 1, "el aislamiento no quedó registrado en audit_logs"
    assert aislamientos[0]["usuario_id"] is not None, "no consta quién ordenó la cuarentena"
    assert aislamientos[0]["detalle"]["registro_corrupto_id"] == registro_id


async def test_tecnico_no_puede_aislar_corrupcion(client, token_tecnico, db_session_factory):
    _ingestar_lectura(client, token_tecnico, "esp32-corrupto-05")
    registro_id = await _corromper_primer_registro(db_session_factory)
    client.get("/api/trazabilidad/verificar", headers=auth_header(token_tecnico))

    response = client.post(
        f"/api/trazabilidad/corrupcion/{registro_id}/aislar", headers=auth_header(token_tecnico)
    )
    assert response.status_code == 403


async def test_aislamiento_marca_registro_corrupto_en_bd(client, token_admin, token_tecnico, db_session_factory):
    _ingestar_lectura(client, token_tecnico, "esp32-corrupto-06")
    registro_id = await _corromper_primer_registro(db_session_factory)
    client.get("/api/trazabilidad/verificar", headers=auth_header(token_tecnico))
    client.post(f"/api/trazabilidad/corrupcion/{registro_id}/aislar", headers=auth_header(token_admin))

    async with db_session_factory() as session:
        import uuid

        model = await session.get(TraceabilityRecordModel, uuid.UUID(registro_id))
        assert model.is_corrupted is True
