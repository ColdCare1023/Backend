"""HU-42 esc. 3 y HU-45 esc. 3 (pruebas que faltaban en HU que cumplen).

- HU-42: la bitácora no se modifica ni se elimina mediante la aplicación: la
  API no expone PUT ni DELETE sobre /api/auditoria (405), ni siquiera para el
  administrador o el auditor, y la bitácora queda igual.
- HU-45: desactivar a un usuario conserva intactos sus eslabones: la
  verificación de las cadenas sigue dando «íntegra» después."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from src.domain.value_objects.rol import Rol
from src.infrastructure.database.models import AuditLogModel, TraceabilityRecordModel
from tests.conftest import auth_header

DEVICE_ID = "FARM-HU45-01"


async def _filas_bitacora(db_session_factory) -> int:
    async with db_session_factory() as session:
        return await session.scalar(select(func.count()).select_from(AuditLogModel))


@pytest.mark.parametrize("metodo", ["PUT", "DELETE"])
@pytest.mark.parametrize("ruta", ["/api/auditoria", "/api/auditoria/exportar"])
async def test_hu42_put_y_delete_sobre_la_bitacora_devuelven_405(
    client, token_admin, token_auditor, db_session_factory, metodo, ruta
):
    antes = await _filas_bitacora(db_session_factory)
    for token in (token_admin, token_auditor):
        respuesta = client.request(metodo, ruta, json={"accion": "BORRADO"}, headers=auth_header(token))
        assert respuesta.status_code == 405, (metodo, ruta, respuesta.status_code)
        assert "GET" in respuesta.headers.get("allow", "")
    assert await _filas_bitacora(db_session_factory) == antes


async def test_hu45_la_cadena_sigue_integra_tras_desactivar_al_usuario(
    client, crear_usuario, token_admin, token_tecnico, token_auditor, db_session_factory
):
    qf = await crear_usuario("QF Cesada", "qf.cesada@farmacia.example.org", "password123", Rol.FARMACEUTICO)
    token_qf = client.post(
        "/api/auth/login", data={"username": "qf.cesada@farmacia.example.org", "password": "password123"}
    ).json()["access_token"]
    # Actividad propia en la cadena del dispositivo y en la de SISTEMA.
    lectura = client.post(
        "/api/lecturas",
        json={
            "device_id": DEVICE_ID, "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "temperatura_interna": 11.0, "temperatura_ambiental": 21.0, "humedad_ambiental": 55.0,
            "apertura_refrigerador": False, "estado_conectividad": "online",
        },
        headers=auth_header(token_tecnico),
    )
    assert lectura.status_code == 201
    [alerta] = client.get("/api/alertas", params={"device_id": DEVICE_ID}, headers=auth_header(token_qf)).json()
    assert client.post(
        f"/api/alertas/{alerta['id']}/acciones-correctivas",
        json={"decision": "Trasladar el lote", "descripcion": "Traslado al equipo de respaldo.", "justificacion": "Fuera de rango"},
        headers=auth_header(token_qf),
    ).status_code == 201

    async with db_session_factory() as session:
        suyos_antes = {
            (r.id, r.hash_actual)
            for r in (
                await session.execute(select(TraceabilityRecordModel).where(TraceabilityRecordModel.usuario_id == qf.id))
            ).scalars()
        }
    assert suyos_antes

    respuesta = client.patch(
        f"/api/usuarios/{qf.id}/desactivar", json={"motivo": "renuncia"}, headers=auth_header(token_admin)
    )
    assert respuesta.status_code == 200

    async with db_session_factory() as session:
        suyos_despues = {
            (r.id, r.hash_actual)
            for r in (
                await session.execute(select(TraceabilityRecordModel).where(TraceabilityRecordModel.usuario_id == qf.id))
            ).scalars()
        }
    assert suyos_antes <= suyos_despues  # nada se borra ni se reescribe

    todas = client.get("/api/trazabilidad/verificar", headers=auth_header(token_auditor)).json()
    assert todas["integra"] is True and todas["estado"] == "integra", todas
    for chain_id in (DEVICE_ID, "SISTEMA"):
        cadena = client.get(
            "/api/trazabilidad/verificar", params={"chain_id": chain_id}, headers=auth_header(token_auditor)
        ).json()
        assert cadena["integra"] is True, (chain_id, cadena)
