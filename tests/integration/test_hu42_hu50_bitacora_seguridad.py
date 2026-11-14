"""HU-42 y HU-50: resultado de cada evento, denegaciones RBAC y sesiones
rechazadas auditadas, LOGIN_FALLIDO correlacionable por actor, filtros de la
bitácora y exportación CSV auditada.

HU-42 esc. 1: actor, timestamp, acción, RESULTADO y recurso. HU-50 esc. 1:
fallos de login, bloqueos temporales y denegaciones RBAC filtrables por
periodo, usuario o tipo. Esc. 3: rechazos de HU-39/41/45 correlacionables por
actor/IP, timestamp y resultado. Esc. 4: la exportación declara su alcance."""

import csv
import io
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from src.domain.value_objects.resultado_auditoria import ResultadoAuditoria, resultado_de_accion
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.models import AuditLogModel, TraceabilityRecordModel
from src.interface.api.auditoria_router import _celda
from src.interface.limitador_auditoria import LIMITE_AUDITORIA
from tests.conftest import auth_header


def _bitacora(client, token, **params) -> list[dict]:
    params.setdefault("limite", 1000)
    respuesta = client.get("/api/auditoria", params=params, headers=auth_header(token))
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _id_de(client, token_admin, rol: str) -> str:
    usuarios = client.get("/api/usuarios", headers=auth_header(token_admin)).json()
    return next(u["id"] for u in usuarios if u["rol"] == rol)


# ── HU-42 esc. 1: resultado expuesto y filtrable ──────────────────────────


def test_cada_evento_expone_su_resultado(client, token_admin):
    client.post("/api/auth/login", data={"username": "nadie@farmacia.example.org", "password": "malamala1"})

    filas = _bitacora(client, token_admin)

    assert all(f["resultado"] in {"exitoso", "fallido", "denegado", "bloqueado"} for f in filas)
    por_accion = {f["accion"]: f["resultado"] for f in filas}
    assert por_accion["LOGIN_FALLIDO"] == "fallido"
    assert por_accion["LOGIN_EXITOSO"] == "exitoso"


def test_filtro_por_resultado(client, token_admin, token_tecnico):
    client.post("/api/auth/login", data={"username": "nadie@farmacia.example.org", "password": "malamala1"})
    client.get("/api/usuarios", headers=auth_header(token_tecnico))

    fallidas = _bitacora(client, token_admin, resultado="fallido")
    denegadas = _bitacora(client, token_admin, resultado="denegado")
    exitosas = _bitacora(client, token_admin, resultado="exitoso")

    assert {f["accion"] for f in fallidas} == {"LOGIN_FALLIDO"}
    assert {f["accion"] for f in denegadas} == {"RBAC_DENEGADO"}
    assert "LOGIN_EXITOSO" in {f["accion"] for f in exitosas}
    assert all(f["resultado"] == "exitoso" for f in exitosas)
    assert client.get(
        "/api/auditoria", params={"resultado": "raro"}, headers=auth_header(token_admin)
    ).status_code == 422


async def test_filas_anteriores_a_la_migracion_derivan_su_resultado(client, token_admin, db_session_factory):
    """audit_logs no se reescribe: una fila sin `resultado` (anterior a 0039)
    se presenta y se filtra con el resultado que corresponde a su acción."""
    async with db_session_factory() as session:
        session.add_all(
            [
                AuditLogModel(accion="LOGIN_FALLIDO", recurso="auth/login", detalle={}, resultado=None),
                AuditLogModel(accion="UMBRAL_INTENTOS_FALLIDOS_EXCEDIDO", recurso="auth/login", detalle={}),
                AuditLogModel(accion="CREAR_USUARIO", recurso="usuarios", detalle={}, resultado=None),
            ]
        )
        await session.commit()

    legado = {f["accion"]: f["resultado"] for f in _bitacora(client, token_admin)}
    assert legado["LOGIN_FALLIDO"] == "fallido"
    assert legado["UMBRAL_INTENTOS_FALLIDOS_EXCEDIDO"] == "bloqueado"
    assert legado["CREAR_USUARIO"] == "exitoso"
    assert {f["accion"] for f in _bitacora(client, token_admin, resultado="bloqueado")} == {
        "UMBRAL_INTENTOS_FALLIDOS_EXCEDIDO"
    }
    assert "CREAR_USUARIO" in {f["accion"] for f in _bitacora(client, token_admin, resultado="exitoso")}
    assert "LOGIN_FALLIDO" not in {f["accion"] for f in _bitacora(client, token_admin, resultado="exitoso")}


def test_resultado_de_accion_cubre_los_rechazos_conocidos():
    assert resultado_de_accion("LOGIN_CODIGO_FALLIDO") == ResultadoAuditoria.FALLIDO
    assert resultado_de_accion("RBAC_DENEGADO") == ResultadoAuditoria.DENEGADO
    assert resultado_de_accion("SESION_RECHAZADA") == ResultadoAuditoria.DENEGADO
    assert resultado_de_accion("LECTURA_RECHAZADA_CREDENCIAL_REVOCADA") == ResultadoAuditoria.DENEGADO
    assert resultado_de_accion("UMBRAL_INTENTOS_FALLIDOS_EXCEDIDO") == ResultadoAuditoria.BLOQUEADO
    assert resultado_de_accion("ALTA_DISPOSITIVO") == ResultadoAuditoria.EXITOSO


def test_filtros_por_periodo_y_actor(client, token_admin, token_auditor):
    admin_id = _id_de(client, token_admin, "administrador")
    ahora = datetime.now(tz=timezone.utc)

    por_actor = _bitacora(client, token_auditor, usuario_id=admin_id)
    assert por_actor and all(f["usuario_id"] == admin_id for f in por_actor)
    futuro = (ahora + timedelta(days=1)).isoformat()
    pasado = (ahora - timedelta(days=1)).isoformat()
    assert _bitacora(client, token_auditor, desde=futuro) == []
    assert _bitacora(client, token_auditor, hasta=pasado) == []
    assert _bitacora(client, token_auditor, desde=pasado, hasta=futuro)


# ── HU-50 esc. 1 y 3: denegaciones RBAC auditadas con tope de volumen ─────


async def test_un_403_de_rbac_queda_auditado_con_actor_ip_y_ruta(client, token_admin, token_tecnico, db_session_factory):
    assert client.get("/api/usuarios", headers=auth_header(token_tecnico)).status_code == 403
    tecnico_id = _id_de(client, token_admin, "tecnico")

    [fila] = _bitacora(client, token_admin, accion="RBAC_DENEGADO")

    assert fila["usuario_id"] == tecnico_id
    assert fila["resultado"] == "denegado"
    assert fila["recurso"] == "GET /api/usuarios"
    assert fila["ip_origen"]
    assert fila["detalle"]["rol"] == "tecnico"
    assert fila["detalle"]["roles_permitidos"] == ["administrador"]
    # HU-42 esc. 2: también encadenada, con actor y resultado bajo el hash.
    async with db_session_factory() as session:
        eslabones = (
            await session.execute(select(TraceabilityRecordModel).where(TraceabilityRecordModel.tipo_evento == "AUDIT_LOG"))
        ).scalars().all()
    [eslabon] = [e for e in eslabones if e.payload["accion"] == "RBAC_DENEGADO"]
    assert eslabon.payload["resultado"] == "denegado"
    assert eslabon.payload["usuario_id"] == tecnico_id


def test_las_denegaciones_repetidas_no_inundan_la_bitacora(client, token_admin, token_tecnico, monkeypatch):
    reloj = [1000.0]
    monkeypatch.setattr(LIMITE_AUDITORIA, "_reloj", lambda: reloj[0])
    for _ in range(5):
        assert client.get("/api/usuarios", headers=auth_header(token_tecnico)).status_code == 403
    assert len(_bitacora(client, token_admin, accion="RBAC_DENEGADO")) == 1

    reloj[0] += 61  # vence la ventana: la siguiente fila resume las suprimidas
    assert client.get("/api/usuarios", headers=auth_header(token_tecnico)).status_code == 403
    filas = _bitacora(client, token_admin, accion="RBAC_DENEGADO")
    assert len(filas) == 2
    assert max(f["detalle"]["intentos_sin_auditar"] for f in filas) == 4
    # Otra ruta es otra clave: se audita aparte.
    assert client.get("/api/auditoria", headers=auth_header(token_tecnico)).status_code == 403
    assert len(_bitacora(client, token_admin, accion="RBAC_DENEGADO")) == 3


# ── HU-50 esc. 3: sesiones rechazadas (HU-45 / BE-009) ────────────────────


def test_token_de_usuario_desactivado_queda_auditado(client, token_admin, token_tecnico):
    tecnico_id = _id_de(client, token_admin, "tecnico")
    client.patch(f"/api/usuarios/{tecnico_id}/desactivar", json={"motivo": "renuncia"}, headers=auth_header(token_admin))

    respuesta = client.get("/api/alertas", headers=auth_header(token_tecnico))

    assert respuesta.status_code == 401
    [fila] = _bitacora(client, token_admin, accion="SESION_RECHAZADA")
    assert fila["usuario_id"] == tecnico_id
    assert fila["resultado"] == "denegado"
    assert fila["detalle"]["motivo"] == "usuario_desactivado"
    assert fila["recurso"] == "GET /api/alertas"


def test_sesion_invalidada_por_cambio_de_rol_queda_auditada(client, token_admin, token_tecnico):
    tecnico_id = _id_de(client, token_admin, "tecnico")
    client.patch(f"/api/usuarios/{tecnico_id}/rol", json={"rol": "auditor"}, headers=auth_header(token_admin))

    assert client.get("/api/alertas", headers=auth_header(token_tecnico)).status_code == 401
    [fila] = _bitacora(client, token_admin, accion="SESION_RECHAZADA")
    assert fila["usuario_id"] == tecnico_id
    assert fila["detalle"]["motivo"] == "sesion_invalidada"


def test_token_revocado_por_logout_queda_auditado(client, token_admin, token_tecnico):
    tecnico_id = _id_de(client, token_admin, "tecnico")
    assert client.post("/api/auth/logout", headers=auth_header(token_tecnico)).status_code in (200, 204)

    assert client.get("/api/alertas", headers=auth_header(token_tecnico)).status_code == 401
    [fila] = _bitacora(client, token_admin, accion="SESION_RECHAZADA")
    assert fila["usuario_id"] == tecnico_id
    assert fila["detalle"]["motivo"] == "token_revocado"


def test_un_token_mal_formado_no_se_audita(client, token_admin):
    assert client.get("/api/alertas", headers=auth_header("no-es-un-jwt")).status_code == 401
    assert _bitacora(client, token_admin, accion="SESION_RECHAZADA") == []


# ── HU-50 esc. 1 y 3: LOGIN_FALLIDO correlacionable por actor ─────────────


async def test_login_fallido_de_cuenta_existente_lleva_su_usuario(client, crear_usuario, token_admin):
    victima = await crear_usuario("Víctima", "victima@farmacia.example.org", "password123", Rol.TECNICO)

    respuesta = client.post("/api/auth/login", data={"username": "victima@farmacia.example.org", "password": "malamala1"})
    client.post("/api/auth/login", data={"username": "fantasma@farmacia.example.org", "password": "malamala1"})

    # La respuesta sigue sin revelar si la cuenta existe.
    assert respuesta.status_code == 401
    assert respuesta.json()["detail"] == "Email o contraseña incorrectos"
    [de_la_victima] = _bitacora(client, token_admin, accion="LOGIN_FALLIDO", usuario_id=str(victima.id))
    assert de_la_victima["detalle"]["motivo"] == "password_incorrecta"
    assert de_la_victima["detalle"]["email"] == "v***@farmacia.example.org"
    sin_cuenta = [f for f in _bitacora(client, token_admin, accion="LOGIN_FALLIDO") if f["usuario_id"] is None]
    assert sin_cuenta[0]["detalle"]["motivo"] == "cuenta_inexistente"


async def test_login_de_usuario_desactivado_se_correlaciona_por_actor(client, crear_usuario, token_admin):
    cesado = await crear_usuario("Cesado", "cesado@farmacia.example.org", "password123", Rol.TECNICO)
    client.patch(f"/api/usuarios/{cesado.id}/desactivar", json={"motivo": "renuncia"}, headers=auth_header(token_admin))

    respuesta = client.post("/api/auth/login", data={"username": "cesado@farmacia.example.org", "password": "password123"})

    assert respuesta.status_code == 401
    [fila] = _bitacora(client, token_admin, accion="LOGIN_FALLIDO", usuario_id=str(cesado.id))
    assert fila["detalle"]["motivo"] == "usuario_desactivado"
    assert fila["resultado"] == "fallido"


async def test_el_bloqueo_de_una_cuenta_se_audita_como_bloqueado_y_con_su_usuario(
    client, crear_usuario, token_admin
):
    titular = await crear_usuario("Insistente", "insistente@farmacia.example.org", "password123", Rol.TECNICO)
    limitador_cuenta = client.app.state.login_cuenta_rate_limiter
    for _ in range(20):
        limitador_cuenta.registrar_fallo("insistente@farmacia.example.org")

    respuesta = client.post("/api/auth/login", data={"username": "insistente@farmacia.example.org", "password": "x" * 8})

    assert respuesta.status_code == 429
    [fila] = _bitacora(client, token_admin, accion="UMBRAL_INTENTOS_FALLIDOS_EXCEDIDO")
    assert fila["resultado"] == "bloqueado"
    assert fila["usuario_id"] == str(titular.id)
    assert fila["detalle"]["alcance"] == "cuenta"
    assert "ataque" not in str(fila).lower()


# ── HU-42 / HU-50 esc. 4: exportación CSV auditada ────────────────────────


def test_exportar_la_bitacora_en_csv_queda_auditado(client, token_admin, token_auditor):
    client.post("/api/auth/login", data={"username": "nadie@farmacia.example.org", "password": "malamala1"})

    respuesta = client.get("/api/auditoria/exportar", params={"resultado": "fallido"}, headers=auth_header(token_auditor))

    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/csv")
    assert "attachment" in respuesta.headers["content-disposition"]
    texto = respuesta.content.decode("utf-8-sig")
    primera, resto = texto.split("\n", 1)
    assert primera.startswith("#") and "No constituye una certificación de seguridad" in primera
    filas = list(csv.DictReader(io.StringIO(resto)))
    assert filas and {f["accion"] for f in filas} == {"LOGIN_FALLIDO"}
    assert {f["resultado"] for f in filas} == {"fallido"}

    [exportacion] = _bitacora(client, token_admin, accion="EXPORTAR_BITACORA")
    assert exportacion["detalle"]["filtros"]["resultado"] == "fallido"
    assert exportacion["detalle"]["filas"] == len(filas)
    assert exportacion["resultado"] == "exitoso"


def test_solo_admin_y_auditor_exportan(client, token_tecnico, token_farmaceutico):
    assert client.get("/api/auditoria/exportar", headers=auth_header(token_tecnico)).status_code == 403
    assert client.get("/api/auditoria/exportar", headers=auth_header(token_farmaceutico)).status_code == 403


@pytest.mark.parametrize("valor", ["=SUMA(A1)", "+1", "-1", "@cmd", "\tx"])
def test_el_csv_neutraliza_formulas(valor):
    assert _celda(valor).startswith("'")
    assert _celda("LOGIN_FALLIDO") == "LOGIN_FALLIDO"


# ── HU-42 esc. 2: cambios de configuración encadenados en su chain_id ─────


def test_el_cambio_de_intervalo_se_encadena_en_la_cadena_del_dispositivo(client, token_admin, token_auditor):
    device = "ESP32-HU42-INT"
    assert client.post(
        "/api/dispositivos", json={"device_id": device, "sensores_habilitados": ["ds18b20"]}, headers=auth_header(token_admin)
    ).status_code == 201
    assert client.patch(
        f"/api/dispositivos/{device}/intervalo", json={"segundos": 45}, headers=auth_header(token_admin)
    ).status_code == 200

    cadena = client.get(
        "/api/trazabilidad", params={"chain_id": device, "limite": 1000}, headers=auth_header(token_auditor)
    ).json()
    acciones = [r["payload"].get("accion") for r in cadena if r["tipo_evento"] == "AUDIT_LOG"]
    assert "INTERVALO_REPORTE_ACTUALIZADO" in acciones
    verificacion = client.get(
        "/api/trazabilidad/verificar", params={"chain_id": device}, headers=auth_header(token_auditor)
    ).json()
    assert verificacion["estado"] == "integra"
