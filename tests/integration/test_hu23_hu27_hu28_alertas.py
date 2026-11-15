"""HU-23 (máquina de estados PENDIENTE/RECONOCIDA/ATENDIDA de alertas
críticas) y HU-27/HU-28 (concurrencia y rectificación de acciones
correctivas) del backlog de 51 HU finales."""

from datetime import datetime, timezone

import pytest_asyncio
from sqlalchemy import select

from src.domain.value_objects.rol import Rol
from src.infrastructure.database.models import AuditLogModel, TraceabilityRecordModel
from tests.conftest import auth_header

DEVICE_ID = "FARM-HU23-01"


def _payload(**overrides) -> dict:
    base = {
        "device_id": DEVICE_ID,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "temperatura_interna": 15.0,  # fuera de rango: excursión crítica
        "temperatura_ambiental": 21.0,
        "humedad_ambiental": 55.0,
        "apertura_refrigerador": False,
        "estado_conectividad": "online",
    }
    base.update(overrides)
    return base


def _crear_alerta_critica(client, token) -> str:
    respuesta = client.post("/api/lecturas", json=_payload(), headers=auth_header(token))
    assert respuesta.status_code == 201
    alertas = client.get(
        "/api/alertas", params={"device_id": DEVICE_ID}, headers=auth_header(token)
    ).json()
    assert len(alertas) == 1
    return alertas[0]["id"]


def test_alerta_nace_pendiente(client, token_tecnico):
    alerta_id = _crear_alerta_critica(client, token_tecnico)
    alerta = client.get(
        "/api/alertas", params={"device_id": DEVICE_ID}, headers=auth_header(token_tecnico)
    ).json()[0]
    assert alerta["id"] == alerta_id
    assert alerta["estado"] == "pendiente"
    assert alerta["reconocida_en"] is None
    assert alerta["atendida_en"] is None


def test_reconocer_transiciona_a_reconocida(client, token_tecnico, token_farmaceutico):
    alerta_id = _crear_alerta_critica(client, token_tecnico)

    respuesta = client.patch(
        f"/api/alertas/{alerta_id}/revisar", headers=auth_header(token_farmaceutico)
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["estado"] == "reconocida"
    assert cuerpo["reconocida_en"] is not None
    assert cuerpo["revisada"] is True


def test_reconocer_dos_veces_es_conflicto(client, token_tecnico, token_farmaceutico):
    alerta_id = _crear_alerta_critica(client, token_tecnico)
    client.patch(f"/api/alertas/{alerta_id}/revisar", headers=auth_header(token_farmaceutico))

    segunda = client.patch(f"/api/alertas/{alerta_id}/revisar", headers=auth_header(token_farmaceutico))
    assert segunda.status_code == 409


_ACCION = {
    "decision": "Trasladar el lote",
    "justificacion": "Lectura fuera de rango",
    "descripcion": "Se trasladaron los medicamentos a un refrigerador de respaldo.",
}


@pytest_asyncio.fixture
async def token_otro_farmaceutico(crear_usuario, client):
    """Un segundo QF de turno: «otro compañero» de HU-27 esc. 2."""
    await crear_usuario("Otra QF", "otra.qf@farmacia.example.org", "password123", Rol.FARMACEUTICO)
    respuesta = client.post(
        "/api/auth/login", data={"username": "otra.qf@farmacia.example.org", "password": "password123"}
    )
    assert respuesta.status_code == 200
    return respuesta.json()["access_token"]


def _accion(client, token, alerta_id, **cambios):
    return client.post(
        f"/api/alertas/{alerta_id}/acciones-correctivas", json={**_ACCION, **cambios}, headers=auth_header(token)
    )


def _alerta(client, token) -> dict:
    return client.get("/api/alertas", params={"device_id": DEVICE_ID}, headers=auth_header(token)).json()[0]


async def _cadena_del_dispositivo(db_session_factory) -> list[TraceabilityRecordModel]:
    async with db_session_factory() as session:
        return (
            await session.execute(
                select(TraceabilityRecordModel)
                .where(TraceabilityRecordModel.chain_id == DEVICE_ID)
                .order_by(TraceabilityRecordModel.chain_seq)
            )
        ).scalars().all()


def _es_revisar_alerta(registro) -> bool:
    return registro.tipo_evento == "AUDIT_LOG" and registro.payload.get("accion") == "REVISAR_ALERTA"


def test_registrar_accion_correctiva_transiciona_a_atendida(client, token_tecnico, token_farmaceutico):
    alerta_id = _crear_alerta_critica(client, token_tecnico)
    client.patch(f"/api/alertas/{alerta_id}/revisar", headers=auth_header(token_farmaceutico))

    respuesta = _accion(client, token_farmaceutico, alerta_id)
    assert respuesta.status_code == 201

    alerta = _alerta(client, token_tecnico)
    assert alerta["estado"] == "atendida"
    assert alerta["atendida_en"] is not None


def test_accion_correctiva_concurrente_sobre_alerta_atendida_es_rechazada(
    client, token_tecnico, token_farmaceutico, token_otro_farmaceutico
):
    """HU-27 Escenario 2: dos QF intentan atender la misma alerta RECONOCIDA
    casi a la vez — el segundo debe recibir 409, la alerta ya no está vigente."""
    alerta_id = _crear_alerta_critica(client, token_tecnico)
    assert client.patch(
        f"/api/alertas/{alerta_id}/revisar", headers=auth_header(token_farmaceutico)
    ).status_code == 200

    primera = _accion(client, token_farmaceutico, alerta_id, descripcion="Primera acción: se documentó la excursión.")
    assert primera.status_code == 201

    segunda = _accion(client, token_otro_farmaceutico, alerta_id, descripcion="Segunda acción, llegó tarde.")
    assert segunda.status_code == 409
    ciclo = client.get(f"/api/alertas/{alerta_id}/acciones-correctivas", headers=auth_header(token_tecnico)).json()
    assert [a["id"] for a in ciclo] == [primera.json()["id"]]


def test_accion_correctiva_sin_descripcion_se_rechaza(client, token_tecnico, token_farmaceutico):
    alerta_id = _crear_alerta_critica(client, token_tecnico)

    respuesta = _accion(client, token_farmaceutico, alerta_id, descripcion="")
    assert respuesta.status_code == 422
    # La validación ocurre antes del caso de uso: la alerta no se reconoce.
    assert _alerta(client, token_tecnico)["estado"] == "pendiente"


def test_rectificar_accion_correctiva_crea_evento_nuevo_sin_sobrescribir(client, token_tecnico, token_farmaceutico):
    alerta_id = _crear_alerta_critica(client, token_tecnico)
    original = _accion(client, token_farmaceutico, alerta_id, descripcion="Descripción con un error de tipeo.").json()

    rectificacion = client.post(
        f"/api/alertas/acciones-correctivas/{original['id']}/rectificar",
        json={
            "descripcion": "Descripción corregida, sin el error.",
            "motivo_rectificacion": "Error de tipeo en la versión original.",
        },
        headers=auth_header(token_farmaceutico),
    )
    assert rectificacion.status_code == 201
    cuerpo = rectificacion.json()
    assert cuerpo["corrige_accion_id"] == original["id"]
    assert cuerpo["id"] != original["id"]
    assert cuerpo["descripcion"] == "Descripción corregida, sin el error."

    # La acción original NO se modificó.
    trazabilidad = client.get(
        "/api/trazabilidad", params={"tipo_evento": "ACCION_CORRECTIVA_RECTIFICADA"},
        headers=auth_header(token_tecnico),
    ).json()
    assert len(trazabilidad) == 1


# ── H16 (HU-27 / HU-41): solo el QF registra la acción correctiva ─────────


async def test_el_tecnico_no_registra_acciones_ni_reconoce_por_esa_via(client, token_tecnico, db_session_factory):
    alerta_id = _crear_alerta_critica(client, token_tecnico)

    respuesta = _accion(client, token_tecnico, alerta_id)

    assert respuesta.status_code == 403
    alerta = _alerta(client, token_tecnico)
    assert (alerta["estado"], alerta["reconocida_en"], alerta["revisada_por"]) == ("pendiente", None, None)
    assert not any(r.tipo_evento == "ACCION_CORRECTIVA" for r in await _cadena_del_dispositivo(db_session_factory))
    async with db_session_factory() as session:
        denegaciones = (
            await session.execute(select(AuditLogModel).where(AuditLogModel.accion == "RBAC_DENEGADO"))
        ).scalars().all()
    assert len(denegaciones) == 1


def test_el_administrador_conserva_su_acceso_implicito(client, token_tecnico, token_admin):
    """rbac.verificar_permiso: el administrador accede a toda ruta protegida;
    la matriz de HU-41 lista solo los roles explícitos ({farmaceutico})."""
    alerta_id = _crear_alerta_critica(client, token_tecnico)
    assert _accion(client, token_admin, alerta_id).status_code == 201


# ── HU-23 esc. 1/3 + HU-27 esc. 1: acción sobre una alerta PENDIENTE ──────


async def test_accion_sobre_alerta_pendiente_la_reconoce_antes_de_atenderla(
    client, token_tecnico, token_farmaceutico, db_session_factory
):
    alerta_id = _crear_alerta_critica(client, token_tecnico)

    respuesta = _accion(client, token_farmaceutico, alerta_id)

    assert respuesta.status_code == 201
    accion = respuesta.json()
    alerta = _alerta(client, token_tecnico)
    assert alerta["estado"] == "atendida"
    assert alerta["revisada"] is True
    assert alerta["revisada_por"] == accion["usuario_id"]
    assert alerta["revisada_por_nombre"] == "Farmaceutico Test"
    assert alerta["reconocida_en"] is not None
    assert alerta["reconocida_en"] <= alerta["atendida_en"]

    # Cadena del dispositivo: REVISAR_ALERTA (origen accion_correctiva) →
    # ACCION_CORRECTIVA → REGISTRAR_ACCION_CORRECTIVA, en ese orden.
    cadena = await _cadena_del_dispositivo(db_session_factory)
    revisar = [i for i, r in enumerate(cadena) if _es_revisar_alerta(r)]
    accion_i = [i for i, r in enumerate(cadena) if r.tipo_evento == "ACCION_CORRECTIVA"]
    registrar = [
        i for i, r in enumerate(cadena)
        if r.tipo_evento == "AUDIT_LOG" and r.payload.get("accion") == "REGISTRAR_ACCION_CORRECTIVA"
    ]
    assert len(revisar) == len(accion_i) == len(registrar) == 1
    assert revisar[0] < accion_i[0] < registrar[0]
    eslabon = cadena[revisar[0]]
    assert eslabon.payload["detalle"] == {"nivel_riesgo": "excursion_critica", "origen": "accion_correctiva"}
    assert eslabon.payload["recurso"] == f"alertas/{alerta_id}"
    assert str(eslabon.usuario_id) == accion["usuario_id"]
    assert cadena[accion_i[0]].payload["accion_id"] == accion["id"]

    async with db_session_factory() as session:
        [bitacora] = (
            await session.execute(select(AuditLogModel).where(AuditLogModel.accion == "REVISAR_ALERTA"))
        ).scalars().all()
    assert bitacora.detalle["origen"] == "accion_correctiva"
    assert str(bitacora.usuario_id) == accion["usuario_id"]

    verificacion = client.get(
        "/api/trazabilidad/verificar", params={"chain_id": DEVICE_ID}, headers=auth_header(token_farmaceutico)
    ).json()
    assert verificacion["integra"] is True


async def test_accion_sobre_alerta_reconocida_no_cambia_quien_la_reconocio(
    client, token_tecnico, token_farmaceutico, token_otro_farmaceutico, db_session_factory
):
    alerta_id = _crear_alerta_critica(client, token_tecnico)
    reconocida = client.patch(f"/api/alertas/{alerta_id}/revisar", headers=auth_header(token_farmaceutico)).json()

    assert _accion(client, token_otro_farmaceutico, alerta_id).status_code == 201

    alerta = _alerta(client, token_tecnico)
    assert alerta["estado"] == "atendida"
    assert alerta["revisada_por"] == reconocida["revisada_por"]
    assert alerta["reconocida_en"] == reconocida["reconocida_en"]
    revisiones = [r for r in await _cadena_del_dispositivo(db_session_factory) if _es_revisar_alerta(r)]
    assert len(revisiones) == 1
    assert "origen" not in revisiones[0].payload["detalle"]


async def test_dos_acciones_sobre_una_pendiente_solo_una_la_reconoce_y_atiende(
    client, token_tecnico, token_farmaceutico, token_otro_farmaceutico, db_session_factory
):
    alerta_id = _crear_alerta_critica(client, token_tecnico)

    primera = _accion(client, token_farmaceutico, alerta_id)
    segunda = _accion(client, token_otro_farmaceutico, alerta_id)

    assert (primera.status_code, segunda.status_code) == (201, 409)
    assert _alerta(client, token_tecnico)["revisada_por"] == primera.json()["usuario_id"]
    cadena = await _cadena_del_dispositivo(db_session_factory)
    assert sum(_es_revisar_alerta(r) for r in cadena) == 1
    assert sum(r.tipo_evento == "ACCION_CORRECTIVA" for r in cadena) == 1
