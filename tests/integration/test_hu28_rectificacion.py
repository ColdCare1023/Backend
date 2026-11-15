"""HU-28: trazabilidad de acciones correctivas (rectificación sin sobrescribir)."""

from datetime import datetime, timezone
from uuid import UUID

import pytest

from src.application.use_cases.registrar_accion_correctiva import RegistrarAccionCorrectivaUseCase
from src.infrastructure.database.models import CorrectiveActionModel
from src.infrastructure.database.repositories.accion_correctiva_repository import (
    SQLAlchemyAccionCorrectivaRepository,
)
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.infrastructure.database.repositories.trazabilidad_repository import SQLAlchemyTrazabilidadRepository
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
        f"/api/alertas/{alerta_id}/acciones-correctivas", json={"decision": "Trasladar el lote", "justificacion": "Lectura fuera de rango", "descripcion": texto}, headers=auth_header(token)
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
    client, token_farmaceutico, db_session_factory
):
    alerta_id = _crear_alerta(client, token_farmaceutico)
    original = _accion(client, token_farmaceutico, alerta_id)

    respuesta = _rectificar(client, token_farmaceutico, original["id"])
    assert respuesta.status_code == 201
    rectificacion = respuesta.json()
    assert rectificacion["corrige_accion_id"] == original["id"]
    assert rectificacion["motivo_rectificacion"] == MOTIVO

    async with db_session_factory() as session:
        fila = await session.get(CorrectiveActionModel, UUID(original["id"]))
    assert fila.descripcion == original["descripcion"]
    assert fila.motivo_rectificacion is None


@pytest.mark.asyncio
async def test_los_recursos_auditados_caben_en_la_columna(client, token_farmaceutico, db_session_factory):
    """audit_logs.recurso es VARCHAR(100). SQLite no lo valida, PostgreSQL sí:
    un recurso más largo convertía cada acción correctiva en un 500."""
    from sqlalchemy import select

    from src.infrastructure.database.models import AuditLogModel

    alerta_id = _crear_alerta(client, token_farmaceutico)
    accion = _accion(client, token_farmaceutico, alerta_id)
    _rectificar(client, token_farmaceutico, accion["id"])
    async with db_session_factory() as session:
        recursos = (await session.execute(select(AuditLogModel.recurso))).scalars().all()
    assert recursos
    assert max(len(r) for r in recursos) <= AuditLogModel.__table__.c.recurso.type.length


def test_solo_se_rectifica_la_version_vigente(client, token_farmaceutico):
    alerta_id = _crear_alerta(client, token_farmaceutico)
    original = _accion(client, token_farmaceutico, alerta_id)
    primera = _rectificar(client, token_farmaceutico, original["id"]).json()

    # Rectificar otra vez la original bifurcaría la historia.
    assert _rectificar(client, token_farmaceutico, original["id"]).status_code == 409
    # La rectificación vigente sí puede rectificarse.
    assert _rectificar(client, token_farmaceutico, primera["id"], texto="Segunda corrección.").status_code == 201


def test_la_cronologia_marca_vigencia_y_nombres(client, token_farmaceutico):
    alerta_id = _crear_alerta(client, token_farmaceutico)
    original = _accion(client, token_farmaceutico, alerta_id)
    rectificacion = _rectificar(client, token_farmaceutico, original["id"]).json()

    ciclo = client.get(f"/api/alertas/{alerta_id}/acciones-correctivas", headers=auth_header(token_farmaceutico)).json()
    por_id = {a["id"]: a for a in ciclo}
    assert por_id[original["id"]]["vigente"] is False
    assert por_id[original["id"]]["rectificada_por_id"] == rectificacion["id"]
    assert por_id[rectificacion["id"]]["vigente"] is True
    assert {a["usuario_nombre"] for a in ciclo} == {"Farmaceutico Test"}


def test_un_tecnico_no_rectifica_la_accion_de_otro(client, token_tecnico, token_farmaceutico):
    alerta_id = _crear_alerta(client, token_tecnico)
    del_farmaceutico = _accion(client, token_farmaceutico, alerta_id)
    assert _rectificar(client, token_tecnico, del_farmaceutico["id"]).status_code == 403


async def _accion_heredada_de_tecnico(db_session_factory, alerta_id: str) -> str:
    """Acción registrada por un técnico ANTES de H16 (hoy el técnico recibe 403
    en el POST): se crea con el caso de uso, que no aplica RBAC."""
    async with db_session_factory() as session:
        tecnico = await SQLAlchemyUsuarioRepository(session).obtener_por_email("tecnico@farmacia.example.org")
        accion = await RegistrarAccionCorrectivaUseCase(
            SQLAlchemyAccionCorrectivaRepository(session), SQLAlchemyAlertaRepository(session),
            SQLAlchemyTrazabilidadRepository(session), SQLAlchemyAuditLogRepository(session),
        ).execute(UUID(alerta_id), tecnico.id, "Acción heredada del técnico.", "Trasladar el lote", "Fuera de rango")
        await session.commit()
    return str(accion.id)


async def test_un_farmaceutico_si_rectifica_la_accion_heredada_de_un_tecnico(
    client, token_tecnico, token_farmaceutico, db_session_factory
):
    alerta_id = _crear_alerta(client, token_tecnico)
    del_tecnico = await _accion_heredada_de_tecnico(db_session_factory, alerta_id)
    assert _rectificar(client, token_farmaceutico, del_tecnico).status_code == 201


def test_el_auditor_no_puede_rectificar(client, token_farmaceutico, token_auditor):
    alerta_id = _crear_alerta(client, token_farmaceutico)
    accion = _accion(client, token_farmaceutico, alerta_id)
    assert _rectificar(client, token_auditor, accion["id"]).status_code == 403


def test_el_motivo_es_obligatorio(client, token_farmaceutico):
    alerta_id = _crear_alerta(client, token_farmaceutico)
    accion = _accion(client, token_farmaceutico, alerta_id)
    assert _rectificar(client, token_farmaceutico, accion["id"], motivo=None).status_code == 422
    assert _rectificar(client, token_farmaceutico, accion["id"], motivo="corto").status_code == 422


def test_el_eslabon_de_la_rectificacion_cita_el_hash_de_la_original(client, token_farmaceutico):
    alerta_id = _crear_alerta(client, token_farmaceutico)
    original = _accion(client, token_farmaceutico, alerta_id)
    rectificacion = _rectificar(client, token_farmaceutico, original["id"]).json()

    cadena = _cadena(client, token_farmaceutico)
    [eslabon_original] = [
        r for r in cadena["ACCION_CORRECTIVA"] if r["payload"].get("accion_id") == original["id"]
    ]
    [eslabon_rect] = cadena["ACCION_CORRECTIVA_RECTIFICADA"]
    assert eslabon_rect["payload"]["accion_id"] == rectificacion["id"]
    assert eslabon_rect["payload"]["corrige_accion_id"] == original["id"]
    assert eslabon_rect["payload"]["hash_registro_corregido"] == eslabon_original["hash_actual"]


def test_la_cadena_del_dispositivo_sigue_integra(client, token_farmaceutico):
    alerta_id = _crear_alerta(client, token_farmaceutico)
    original = _accion(client, token_farmaceutico, alerta_id)
    _rectificar(client, token_farmaceutico, original["id"])

    verificacion = client.get(
        "/api/trazabilidad/verificar", params={"chain_id": DEVICE_ID}, headers=auth_header(token_farmaceutico)
    ).json()
    assert verificacion["integra"] is True


@pytest.mark.asyncio
async def test_usuario_desactivado_aparece_anonimizado(client, token_farmaceutico, token_admin, db_session_factory):
    alerta_id = _crear_alerta(client, token_farmaceutico)
    _accion(client, token_farmaceutico, alerta_id)
    async with db_session_factory() as session:
        autor = await SQLAlchemyUsuarioRepository(session).obtener_por_email("farmaceutico@farmacia.example.org")
    respuesta = client.patch(
        f"/api/usuarios/{autor.id}/desactivar", json={"motivo": "renuncia"}, headers=auth_header(token_admin)
    )
    assert respuesta.status_code == 200

    ciclo = client.get(f"/api/alertas/{alerta_id}/acciones-correctivas", headers=auth_header(token_admin)).json()
    assert ciclo[0]["usuario_nombre"] == "Usuario desactivado"


# ── HU-28 esc. 2: la rectificación corrige decisión y justificación ───────


def _rectificar_campos(client, token, accion_id, motivo=MOTIVO, **campos):
    return client.post(
        f"/api/alertas/acciones-correctivas/{accion_id}/rectificar",
        json={"motivo_rectificacion": motivo, **campos},
        headers=auth_header(token),
    )


@pytest.mark.asyncio
async def test_corregir_solo_la_justificacion_hereda_lo_demas_y_conserva_la_original(
    client, token_farmaceutico, db_session_factory
):
    alerta_id = _crear_alerta(client, token_farmaceutico)
    original = _accion(client, token_farmaceutico, alerta_id)

    respuesta = _rectificar_campos(
        client, token_farmaceutico, original["id"], justificacion="La lectura superó 8 °C durante 12 min."
    )

    assert respuesta.status_code == 201, respuesta.text
    rectificacion = respuesta.json()
    assert rectificacion["justificacion"] == "La lectura superó 8 °C durante 12 min."
    assert rectificacion["decision"] == original["decision"]
    assert rectificacion["descripcion"] == original["descripcion"]
    async with db_session_factory() as session:
        fila = await session.get(CorrectiveActionModel, UUID(original["id"]))
    assert fila.justificacion == "Lectura fuera de rango"  # la original no se sobrescribe

    [eslabon] = _cadena(client, token_farmaceutico)["ACCION_CORRECTIVA_RECTIFICADA"]
    payload = eslabon["payload"]
    assert payload["campos_corregidos"] == ["justificacion"]
    assert payload["justificacion"] == "La lectura superó 8 °C durante 12 min."
    assert payload["decision"] == original["decision"]
    assert payload["descripcion"] == original["descripcion"]


def test_corregir_la_decision_queda_en_la_cronologia(client, token_farmaceutico):
    alerta_id = _crear_alerta(client, token_farmaceutico)
    original = _accion(client, token_farmaceutico, alerta_id)

    rectificacion = _rectificar_campos(
        client, token_farmaceutico, original["id"], decision="Poner el lote en cuarentena"
    ).json()

    ciclo = client.get(f"/api/alertas/{alerta_id}/acciones-correctivas", headers=auth_header(token_farmaceutico)).json()
    por_id = {a["id"]: a for a in ciclo}
    assert por_id[original["id"]]["decision"] == "Trasladar el lote"
    assert por_id[original["id"]]["vigente"] is False
    assert por_id[rectificacion["id"]]["decision"] == "Poner el lote en cuarentena"
    assert por_id[rectificacion["id"]]["justificacion"] == original["justificacion"]
    assert por_id[rectificacion["id"]]["vigente"] is True


def test_una_rectificacion_sin_cambios_se_rechaza(client, token_farmaceutico):
    alerta_id = _crear_alerta(client, token_farmaceutico)
    original = _accion(client, token_farmaceutico, alerta_id)

    assert _rectificar_campos(client, token_farmaceutico, original["id"]).status_code == 422
    igual = _rectificar_campos(client, token_farmaceutico, original["id"], justificacion=original["justificacion"])
    assert igual.status_code == 422
    assert _cadena(client, token_farmaceutico).get("ACCION_CORRECTIVA_RECTIFICADA") is None


def test_el_ciclo_se_reconstruye_en_orden_por_chain_id(client, token_farmaceutico, token_auditor):
    """Esc. 3: el auditor ordena acción y rectificaciones por chain_seq y
    comprueba su integridad verificando una sola cadena."""
    alerta_id = _crear_alerta(client, token_farmaceutico)
    original = _accion(client, token_farmaceutico, alerta_id)
    primera = _rectificar_campos(client, token_farmaceutico, original["id"], decision="Cuarentena").json()
    segunda = _rectificar_campos(client, token_farmaceutico, primera["id"], justificacion="Corregida dos veces").json()

    registros = client.get(
        "/api/trazabilidad", params={"chain_id": DEVICE_ID, "limite": 500}, headers=auth_header(token_auditor)
    ).json()
    acciones = sorted(
        (r for r in registros if r["tipo_evento"].startswith("ACCION_CORRECTIVA")), key=lambda r: r["chain_seq"]
    )
    assert [r["payload"]["accion_id"] for r in acciones] == [original["id"], primera["id"], segunda["id"]]
    assert acciones[2]["payload"]["hash_registro_corregido"] == acciones[1]["hash_actual"]
    assert acciones[2]["payload"]["decision"] == "Cuarentena"  # heredada de la primera rectificación
    verificacion = client.get(
        "/api/trazabilidad/verificar", params={"chain_id": DEVICE_ID}, headers=auth_header(token_auditor)
    ).json()
    assert verificacion["estado"] == "integra"
