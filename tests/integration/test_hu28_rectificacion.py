"""HU-28: trazabilidad de acciones correctivas (rectificación sin sobrescribir)."""

from datetime import datetime, timezone
from uuid import UUID

import pytest

from src.infrastructure.database.models import CorrectiveActionModel
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from tests.conftest import auth_header

DEVICE_ID = "FARM-HU28-01"
MOTIVO = "Se registró la hora equivocada del traslado."


def _crear_alerta(client, token) -> str:
    respuesta = client.post(
        "/api/lecturas",
        json={
            "device_id": DEVICE_ID,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "temperatura_interna": 15.0,
            "temperatura_ambiental": 21.0,
            "humedad_ambiental": 55.0,
            "apertura_refrigerador": False,
            "estado_conectividad": "online",
        },
        headers=auth_header(token),
    )
    assert respuesta.status_code == 201
    alertas = client.get("/api/alertas", params={"device_id": DEVICE_ID}, headers=auth_header(token)).json()
    return alertas[0]["id"]


def _accion(client, token, alerta_id, texto="Se trasladó el lote al refrigerador de respaldo.") -> dict:
    respuesta = client.post(
        f"/api/alertas/{alerta_id}/acciones-correctivas", json={"descripcion": texto}, headers=auth_header(token)
    )
    assert respuesta.status_code == 201
    return respuesta.json()


def _rectificar(client, token, accion_id, texto="Traslado a las 03:10, no a las 03:40.", motivo=MOTIVO):
    cuerpo = {"descripcion": texto}
    if motivo is not None:
        cuerpo["motivo_rectificacion"] = motivo
    return client.post(
        f"/api/alertas/acciones-correctivas/{accion_id}/rectificar", json=cuerpo, headers=auth_header(token)
    )


def _cadena(client, token) -> dict[str, list[dict]]:
    registros = client.get(
        "/api/trazabilidad", params={"device_id": DEVICE_ID, "limite": 500}, headers=auth_header(token)
    ).json()
    por_tipo: dict[str, list[dict]] = {}
    for registro in registros:
        por_tipo.setdefault(registro["tipo_evento"], []).append(registro)
    return por_tipo


@pytest.mark.asyncio
async def test_la_original_queda_intacta_y_la_rectificacion_la_referencia(
    client, token_tecnico, db_session_factory
):
    alerta_id = _crear_alerta(client, token_tecnico)
    original = _accion(client, token_tecnico, alerta_id)

    respuesta = _rectificar(client, token_tecnico, original["id"])
    assert respuesta.status_code == 201
    rectificacion = respuesta.json()
    assert rectificacion["corrige_accion_id"] == original["id"]
    assert rectificacion["motivo_rectificacion"] == MOTIVO

    async with db_session_factory() as session:
        fila = await session.get(CorrectiveActionModel, UUID(original["id"]))
    assert fila.descripcion == original["descripcion"]
    assert fila.motivo_rectificacion is None


@pytest.mark.asyncio
async def test_los_recursos_auditados_caben_en_la_columna(client, token_tecnico, db_session_factory):
    """audit_logs.recurso es VARCHAR(100). SQLite no lo valida, PostgreSQL sí:
    un recurso más largo convertía cada acción correctiva en un 500."""
    from sqlalchemy import select

    from src.infrastructure.database.models import AuditLogModel

    alerta_id = _crear_alerta(client, token_tecnico)
    accion = _accion(client, token_tecnico, alerta_id)
    _rectificar(client, token_tecnico, accion["id"])
    async with db_session_factory() as session:
        recursos = (await session.execute(select(AuditLogModel.recurso))).scalars().all()
    assert recursos
    assert max(len(r) for r in recursos) <= AuditLogModel.__table__.c.recurso.type.length


def test_solo_se_rectifica_la_version_vigente(client, token_tecnico):
    alerta_id = _crear_alerta(client, token_tecnico)
    original = _accion(client, token_tecnico, alerta_id)
    primera = _rectificar(client, token_tecnico, original["id"]).json()

    # Rectificar otra vez la original bifurcaría la historia.
    assert _rectificar(client, token_tecnico, original["id"]).status_code == 409
    # La rectificación vigente sí puede rectificarse.
    assert _rectificar(client, token_tecnico, primera["id"], texto="Segunda corrección.").status_code == 201


def test_la_cronologia_marca_vigencia_y_nombres(client, token_tecnico):
    alerta_id = _crear_alerta(client, token_tecnico)
    original = _accion(client, token_tecnico, alerta_id)
    rectificacion = _rectificar(client, token_tecnico, original["id"]).json()

    ciclo = client.get(f"/api/alertas/{alerta_id}/acciones-correctivas", headers=auth_header(token_tecnico)).json()
    por_id = {a["id"]: a for a in ciclo}
    assert por_id[original["id"]]["vigente"] is False
    assert por_id[original["id"]]["rectificada_por_id"] == rectificacion["id"]
    assert por_id[rectificacion["id"]]["vigente"] is True
    assert {a["usuario_nombre"] for a in ciclo} == {"Tecnico Test"}


def test_un_tecnico_no_rectifica_la_accion_de_otro(client, token_tecnico, token_farmaceutico):
    alerta_id = _crear_alerta(client, token_tecnico)
    del_farmaceutico = _accion(client, token_farmaceutico, alerta_id)
    assert _rectificar(client, token_tecnico, del_farmaceutico["id"]).status_code == 403


def test_un_farmaceutico_si_rectifica_la_accion_de_un_tecnico(client, token_tecnico, token_farmaceutico):
    alerta_id = _crear_alerta(client, token_tecnico)
    del_tecnico = _accion(client, token_tecnico, alerta_id)
    assert _rectificar(client, token_farmaceutico, del_tecnico["id"]).status_code == 201


def test_el_auditor_no_puede_rectificar(client, token_tecnico, token_auditor):
    alerta_id = _crear_alerta(client, token_tecnico)
    accion = _accion(client, token_tecnico, alerta_id)
    assert _rectificar(client, token_auditor, accion["id"]).status_code == 403


def test_el_motivo_es_obligatorio(client, token_tecnico):
    alerta_id = _crear_alerta(client, token_tecnico)
    accion = _accion(client, token_tecnico, alerta_id)
    assert _rectificar(client, token_tecnico, accion["id"], motivo=None).status_code == 422
    assert _rectificar(client, token_tecnico, accion["id"], motivo="corto").status_code == 422


def test_el_eslabon_de_la_rectificacion_cita_el_hash_de_la_original(client, token_tecnico):
    alerta_id = _crear_alerta(client, token_tecnico)
    original = _accion(client, token_tecnico, alerta_id)
    rectificacion = _rectificar(client, token_tecnico, original["id"]).json()

    cadena = _cadena(client, token_tecnico)
    [eslabon_original] = [
        r for r in cadena["ACCION_CORRECTIVA"] if r["payload"].get("accion_id") == original["id"]
    ]
    [eslabon_rect] = cadena["ACCION_CORRECTIVA_RECTIFICADA"]
    assert eslabon_rect["payload"]["accion_id"] == rectificacion["id"]
    assert eslabon_rect["payload"]["corrige_accion_id"] == original["id"]
    assert eslabon_rect["payload"]["hash_registro_corregido"] == eslabon_original["hash_actual"]


def test_la_cadena_del_dispositivo_sigue_integra(client, token_tecnico):
    alerta_id = _crear_alerta(client, token_tecnico)
    original = _accion(client, token_tecnico, alerta_id)
    _rectificar(client, token_tecnico, original["id"])

    verificacion = client.get(
        "/api/trazabilidad/verificar", params={"chain_id": DEVICE_ID}, headers=auth_header(token_tecnico)
    ).json()
    assert verificacion["integra"] is True


@pytest.mark.asyncio
async def test_usuario_desactivado_aparece_anonimizado(client, token_tecnico, token_admin, db_session_factory):
    alerta_id = _crear_alerta(client, token_tecnico)
    _accion(client, token_tecnico, alerta_id)
    async with db_session_factory() as session:
        tecnico = await SQLAlchemyUsuarioRepository(session).obtener_por_email("tecnico@farmacia.example.org")
    respuesta = client.patch(
        f"/api/usuarios/{tecnico.id}/desactivar", json={"motivo": "renuncia"}, headers=auth_header(token_admin)
    )
    assert respuesta.status_code == 200

    ciclo = client.get(f"/api/alertas/{alerta_id}/acciones-correctivas", headers=auth_header(token_admin)).json()
    assert ciclo[0]["usuario_nombre"] == "Usuario desactivado"
