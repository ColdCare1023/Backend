"""Cierre de las cuatro observaciones que la primera ronda dejó como teóricas.

Cada una se decidió por separado; el razonamiento completo está en
MEJORAS_BACKEND.md. Aquí queda la comprobación ejecutable.
"""

from passlib.context import CryptContext

from src.domain.value_objects.rol import Rol
from src.infrastructure.security.password_hasher import hash_password, verify_password
from src.interface.api.auth_router import enmascarar_email
from src.interface.api.schemas import PASSWORD_MAX_BYTES
from tests.conftest import auth_header

# Contexto aislado, solo-bcrypt, para reproducir el hash heredado sin
# depender del esquema por defecto (que ya es Argon2id) ni deprecar nada
# en el _pwd_context real que usa la aplicación.
_bcrypt_solo = CryptContext(schemes=["bcrypt"])

# ── O-01: truncado silencioso de bcrypt a 72 bytes ────────────────────────
#
# HU-39 (backlog de 51 HU finales): el esquema de hash por defecto pasó de
# bcrypt a Argon2id, que NO trunca la contraseña — bcrypt se conserva solo
# para seguir verificando hashes generados antes de este cambio (ver
# password_hasher.py). El límite de 72 bytes en la API (PASSWORD_MAX_BYTES)
# se mantiene de todos modos: cualquier hash bcrypt heredado en la base
# sigue teniendo esa limitación, y no hay forma de saber desde la API con
# qué esquema se hasheará una contraseña nueva en un despliegue que aún no
# haya migrado por completo.


def test_bcrypt_efectivamente_ignora_lo_que_pasa_de_72_bytes():
    """Demuestra el problema que motivó el límite originalmente: NO es
    teórico. Dos contraseñas distintas que comparten los primeros 72 bytes
    son intercambiables al verificar CONTRA UN HASH BCRYPT (el esquema
    heredado, forzado aquí explícitamente para no depender del default)."""
    original = "A1" + "x" * 78
    impostora = original[:72] + "COLA_COMPLETAMENTE_DISTINTA"

    assert original != impostora
    hash_bcrypt = _bcrypt_solo.hash(original)
    assert verify_password(impostora, hash_bcrypt) is True


def test_argon2id_no_trunca_a_72_bytes():
    """A diferencia de bcrypt, el esquema por defecto (Argon2id) sí
    distingue contraseñas que solo difieren después del byte 72."""
    original = "A1" + "x" * 78
    impostora = original[:72] + "COLA_COMPLETAMENTE_DISTINTA"

    assert verify_password(impostora, hash_password(original)) is False
    assert verify_password(original, hash_password(original)) is True


async def test_contrasena_de_mas_de_72_bytes_se_rechaza_al_crear_usuario(client, token_admin):
    """Se rechaza en el borde en vez de truncar: un 422 explícito es preferible
    a una contraseña que "funciona" con una cola distinta a la escrita."""
    response = client.post(
        "/api/usuarios",
        json={
            "nombre": "Contraseña Larguísima",
            "email": "larga@farmacia.example.org",
            "password": "A1" + "x" * 80,
            "rol": Rol.TECNICO.value,
        },
        headers=auth_header(token_admin),
    )

    assert response.status_code == 422
    assert "72" in response.text


async def test_el_limite_se_mide_en_bytes_no_en_caracteres(client, token_admin):
    """40 caracteres con acento son 80 bytes en UTF-8: contarlos como
    caracteres dejaría pasar justo el caso que bcrypt trunca."""
    response = client.post(
        "/api/usuarios",
        json={
            "nombre": "Acentos",
            "email": "acentos@farmacia.example.org",
            "password": "A1" + "ñ" * 40,
            "rol": Rol.TECNICO.value,
        },
        headers=auth_header(token_admin),
    )

    assert response.status_code == 422


async def test_una_contrasena_normal_larga_sigue_siendo_valida(client, token_admin):
    """El límite no debe estorbar a una contraseña robusta legítima."""
    password = "A1" + "x" * (PASSWORD_MAX_BYTES - 3)
    assert len(password.encode()) <= PASSWORD_MAX_BYTES

    response = client.post(
        "/api/usuarios",
        json={
            "nombre": "Robusta",
            "email": "robusta@farmacia.example.org",
            "password": password,
            "rol": Rol.TECNICO.value,
        },
        headers=auth_header(token_admin),
    )

    assert response.status_code == 201


# ── O-02: el stream SSE sobrevivía al cierre de sesión ────────────────────


async def test_el_ticket_sse_queda_atado_a_la_sesion_que_lo_pidio(client, token_admin):
    """El ticket lleva el `jti` del access token que lo solicitó; es lo que
    permite que el stream sepa si la sesión sigue viva."""
    from src.infrastructure.config import Settings
    from src.infrastructure.security.jwt_handler import JWTHandler

    handler = JWTHandler(Settings())
    ticket = client.post("/api/auth/sse-ticket", headers=auth_header(token_admin)).json()["ticket"]

    datos_ticket = handler.validar_ticket_sse(ticket)
    jti_sesion = handler.decodificar_token(token_admin).jti

    assert datos_ticket.token_padre_jti == jti_sesion


async def test_un_ticket_emitido_antes_del_logout_ya_no_abre_el_stream(client, token_admin):
    """Cerrar sesión invalida los tickets pendientes: si no, quedaría una
    ventana en la que un ticket ya emitido abre un caudal de datos térmicos
    después de que el usuario cerró sesión."""
    ticket = client.post("/api/auth/sse-ticket", headers=auth_header(token_admin)).json()["ticket"]

    assert client.post("/api/auth/logout", headers=auth_header(token_admin)).status_code == 204

    response = client.get("/api/sse/lecturas", params={"ticket": ticket})

    assert response.status_code == 401
    assert "sesión" in response.json()["detail"].lower()


async def test_tras_el_logout_no_se_pueden_emitir_nuevos_tickets(client, token_admin):
    client.post("/api/auth/logout", headers=auth_header(token_admin))

    response = client.post("/api/auth/sse-ticket", headers=auth_header(token_admin))

    assert response.status_code == 401


# ── O-03: correo en claro en la bitácora (Ley 29733) ──────────────────────


def test_enmascarar_email_conserva_inicial_y_dominio():
    assert enmascarar_email("farmaceutico@farmacia.example.org") == "f***@farmacia.example.org"


def test_enmascarar_email_es_estable_para_el_mismo_correo():
    """La correlación de intentos —valor forense de RF-16— exige que el mismo
    correo produzca siempre la misma máscara."""
    assert enmascarar_email("ana@x.pe") == enmascarar_email("ana@x.pe")
    assert enmascarar_email("ana@x.pe") != enmascarar_email("beto@x.pe")


def test_enmascarar_email_tolera_entradas_que_no_son_correos():
    """El campo de login acepta texto libre; un atacante puede enviar
    cualquier cosa y la bitácora no debe romperse por ello."""
    assert "@" not in enmascarar_email("' OR 1=1 --")
    assert enmascarar_email("") .startswith("<sin formato")


async def test_el_login_fallido_no_guarda_el_correo_completo(client, token_admin):
    """Un correo tecleado en un intento fallido puede pertenecer a un tercero
    ajeno al sistema, y `audit_logs` es inmutable: no hay rectificación
    posterior posible."""
    client.post(
        "/api/auth/login",
        data={"username": "victima.ajena@otrodominio.example", "password": "loquesea123"},
    )

    registros = client.get("/api/auditoria", headers=auth_header(token_admin)).json()
    fallidos = [r for r in registros if r["accion"] == "LOGIN_FALLIDO"]

    assert fallidos, "el intento fallido debe quedar registrado (RF-16)"
    detalle = fallidos[0]["detalle"]
    assert "victima.ajena" not in str(detalle)
    # Pero conserva lo que da valor a la bitácora: dominio atacado e IP.
    assert detalle["email"] == "v***@otrodominio.example"
    assert fallidos[0]["ip_origen"] is not None


async def test_el_login_exitoso_no_repite_el_correo(client, token_admin):
    """En un acceso correcto `usuario_id` ya identifica al titular; repetir el
    correo sería dato personal redundante en una bitácora inmutable."""
    registros = client.get("/api/auditoria", headers=auth_header(token_admin)).json()
    exitosos = [r for r in registros if r["accion"] == "LOGIN_EXITOSO"]

    assert exitosos
    assert exitosos[0]["usuario_id"] is not None
    assert "email" not in (exitosos[0]["detalle"] or {})


# ── O-04: asignación masiva / extra="forbid" en la API REST ───────────────


async def test_un_campo_no_declarado_se_rechaza_al_crear_usuario(client, token_admin):
    """OWASP API6. No había explotación real —los casos de uso nunca
    desempaquetan el cuerpo—, pero un cliente desalineado fallaba en silencio."""
    response = client.post(
        "/api/usuarios",
        json={
            "nombre": "Intruso",
            "email": "intruso@farmacia.example.org",
            "password": "password123",
            "rol": Rol.TECNICO.value,
            "is_active": True,
            "id": "00000000-0000-0000-0000-000000000001",
        },
        headers=auth_header(token_admin),
    )

    assert response.status_code == 422


async def test_un_campo_no_declarado_se_rechaza_en_la_ingesta_rest(client, token_tecnico):
    """La vía REST debe aceptar exactamente lo mismo que la vía MQTT, que ya
    tenía el contrato cerrado."""
    from datetime import datetime, timezone

    response = client.post(
        "/api/lecturas",
        json={
            "device_id": "FARM-EXTRA-01",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "temperatura_interna": 5.0,
            "nivel_riesgo": "normal",  # lo decide el modelo, no el cliente
        },
        headers=auth_header(token_tecnico),
    )

    assert response.status_code == 422


async def test_estado_conectividad_arbitrario_se_rechaza(client, token_tecnico):
    """Se declaraba como `str` libre y acababa en una columna String(20),
    donde PostgreSQL lo rechaza con un error de escritura en vez de un 422."""
    from datetime import datetime, timezone

    response = client.post(
        "/api/lecturas",
        json={
            "device_id": "FARM-EXTRA-02",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "temperatura_interna": 5.0,
            "estado_conectividad": "x" * 500,
        },
        headers=auth_header(token_tecnico),
    )

    assert response.status_code == 422
