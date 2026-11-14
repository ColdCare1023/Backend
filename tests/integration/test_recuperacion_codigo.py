"""Recuperación de contraseña por código y activación de cuentas creadas por un administrador."""

import re
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from src.application.use_cases.codigos_verificacion import PoliticaCodigos, ServicioCodigosVerificacion
from src.domain.entities.codigo_verificacion import PropositoCodigo
from src.domain.value_objects.rol import Rol
from src.infrastructure.config import Settings, get_settings
from src.infrastructure.database.models import (
    AuditLogModel,
    CodigoVerificacionModel,
    PasswordResetTokenModel,
    UserModel,
)
from src.infrastructure.database.repositories.codigo_verificacion_repository import (
    SQLAlchemyCodigoVerificacionRepository,
)
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from src.infrastructure.notifications.bienvenida import NotificadorBienvenida
from src.interface.api import deps
from src.interface.api.usuarios_router import get_notificador_bienvenida
from tests.conftest import auth_header

TECNICO = "tecnico@farmacia.example.org"
PASSWORD = "password123"
NUEVA = "OtraClave-2026x"


class _CorreoFalso:
    def __init__(self) -> None:
        self.configurado = True
        self.enviados: list[tuple[str, str, str, str | None]] = []

    async def enviar(self, destino: str, asunto: str, cuerpo: str, html: str | None = None) -> bool:
        self.enviados.append((destino, asunto, cuerpo, html))
        return True

    def ultimo_codigo(self) -> str:
        return re.search(r"^\s+(\d{6})$", self.enviados[-1][2], re.MULTILINE).group(1)

    def ultimo_token(self) -> str:
        return re.search(r"#token=(\S+)", self.enviados[-1][2]).group(1)


@pytest.fixture
def correo(app):
    falso = _CorreoFalso()
    app.dependency_overrides[deps.get_enviador_correo] = lambda: falso
    return falso


def _solicitar(client, email: str = TECNICO):
    return client.post("/api/auth/recuperar-password", json={"email": email})


def _restablecer_con_codigo(client, codigo: str, email: str = TECNICO, nueva: str = NUEVA):
    return client.post(
        "/api/auth/restablecer-password", json={"email": email, "codigo": codigo, "password_nueva": nueva}
    )


def _login(client, email: str, password: str):
    return client.post("/api/auth/login", data={"username": email, "password": password})


def _esperar_al_siguiente_segundo() -> None:
    time.sleep(1.01 - datetime.now(tz=timezone.utc).microsecond / 1_000_000)


async def _acciones(fabrica) -> list[AuditLogModel]:
    async with fabrica() as session:
        return list((await session.execute(select(AuditLogModel))).scalars().all())


# ── Recuperación por código ───────────────────────────────────────────────


async def test_recuperacion_por_codigo_completa(client, token_tecnico, correo, db_session_factory):
    respuesta = _solicitar(client)

    assert respuesta.status_code == 202
    (destino, asunto, texto, html) = correo.enviados[0]
    codigo = correo.ultimo_codigo()
    assert destino == TECNICO and asunto == "[ColdCare] Restablecer contraseña"
    assert codigo not in asunto and codigo in html
    assert "http://localhost:5173/recuperar-contrasena?email=tecnico%40farmacia.example.org&modo=recuperar" in texto
    assert "#token=" in texto  # el enlace heredado sigue llegando

    _esperar_al_siguiente_segundo()
    assert _restablecer_con_codigo(client, codigo).status_code == 204
    # Sesiones cerradas, contraseña nueva vigente, código de un solo uso.
    assert client.get("/api/alertas", headers=auth_header(token_tecnico)).status_code == 401
    assert _login(client, TECNICO, PASSWORD).status_code == 401
    assert _login(client, TECNICO, NUEVA).status_code == 200
    assert _restablecer_con_codigo(client, codigo, nueva="Tercera-clave-9").status_code == 400
    # El enlace del mismo correo también quedó anulado.
    async with db_session_factory() as session:
        assert (await session.execute(select(PasswordResetTokenModel))).scalars().all() == []

    restablecida = [a for a in await _acciones(db_session_factory) if a.accion == "PASSWORD_RESTABLECIDA"]
    assert restablecida[0].detalle == {"metodo": "codigo", "proposito": "recuperacion"}
    solicitada = [a for a in await _acciones(db_session_factory) if a.accion == "RECUPERACION_PASSWORD_SOLICITADA"]
    assert solicitada[0].detalle["email"] == "t***@farmacia.example.org"


async def test_no_permite_enumerar_cuentas(client, token_tecnico, correo):
    existente = _solicitar(client)
    inexistente = _solicitar(client, "nadie@farmacia.example.org")
    mayusculas = _solicitar(client, "TECNICO@farmacia.example.org")

    assert existente.status_code == inexistente.status_code == mayusculas.status_code == 202
    assert existente.json() == inexistente.json() == mayusculas.json()
    assert [destino for destino, *_ in correo.enviados] == [TECNICO, TECNICO]
    # Un correo inexistente con código cualquiera da el mismo error que uno mal escrito.
    a = _restablecer_con_codigo(client, "123456", email="nadie@farmacia.example.org")
    b = _restablecer_con_codigo(client, "000000" if correo.ultimo_codigo() != "000000" else "111111")
    assert a.status_code == b.status_code == 400 and a.json() == b.json()


async def test_cuota_por_cuenta_tres_solicitudes_cada_15_minutos(client, token_tecnico, correo, db_session_factory):
    respuestas = [_solicitar(client) for _ in range(4)]

    assert all(r.status_code == 202 for r in respuestas)
    assert len({r.text for r in respuestas}) == 1  # misma respuesta
    assert len(correo.enviados) == 3  # la cuarta no envía nada
    limitadas = [
        a for a in await _acciones(db_session_factory)
        if a.accion == "RECUPERACION_PASSWORD_SOLICITADA" and a.detalle.get("limitada_por_cuenta")
    ]
    assert len(limitadas) == 1


async def test_codigo_incorrecto_cuenta_intentos_en_la_base_y_se_quema(client, token_tecnico, correo, db_session_factory):
    _solicitar(client)
    codigo = correo.ultimo_codigo()
    incorrecto = "000000" if codigo != "000000" else "111111"

    for _ in range(5):
        assert _restablecer_con_codigo(client, incorrecto).status_code == 400
    assert _restablecer_con_codigo(client, codigo).status_code == 400  # quemado
    async with db_session_factory() as session:
        (fila,) = (await session.execute(select(CodigoVerificacionModel))).scalars().all()
    assert fila.intentos == 5 and fila.usado_en is not None
    assert _login(client, TECNICO, PASSWORD).status_code == 200  # la contraseña no cambió
    assert "RESTABLECER_CODIGO_FALLIDO" in {a.accion for a in await _acciones(db_session_factory)}


async def test_codigo_caducado_se_rechaza(client, token_tecnico, correo, db_session_factory):
    _solicitar(client)
    async with db_session_factory() as session:
        await session.execute(
            update(CodigoVerificacionModel).values(expira_en=datetime.now(tz=timezone.utc) - timedelta(seconds=1))
        )
        await session.commit()

    assert _restablecer_con_codigo(client, correo.ultimo_codigo()).status_code == 400
    assert _login(client, TECNICO, PASSWORD).status_code == 200  # la contraseña no cambió


async def test_el_enlace_heredado_sigue_funcionando_y_anula_el_codigo(client, token_tecnico, correo):
    _solicitar(client)
    token, codigo = correo.ultimo_token(), correo.ultimo_codigo()

    legado = client.post("/api/auth/restablecer-password", json={"token": token, "password_nueva": NUEVA})
    assert legado.status_code == 204
    assert _login(client, TECNICO, NUEVA).status_code == 200
    assert _restablecer_con_codigo(client, codigo, nueva="Tercera-clave-9").status_code == 400


async def test_acepta_nueva_password_como_sinonimo(client, token_tecnico, correo):
    _solicitar(client)
    respuesta = client.post(
        "/api/auth/restablecer-password",
        json={"email": TECNICO, "codigo": correo.ultimo_codigo(), "nueva_password": NUEVA},
    )
    assert respuesta.status_code == 204


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"password_nueva": NUEVA},
        {"email": TECNICO, "password_nueva": NUEVA},
        {"codigo": "123456", "password_nueva": NUEVA},
        {"token": "t" * 30, "email": TECNICO, "codigo": "123456", "password_nueva": NUEVA},
        {"email": TECNICO, "codigo": "123456", "password_nueva": "sololetrasaqui"},
        {"email": TECNICO, "codigo": "123456", "password_nueva": "corta1"},
    ],
)
def test_el_cuerpo_exige_token_o_correo_con_codigo_y_la_politica(client, cuerpo):
    assert client.post("/api/auth/restablecer-password", json=cuerpo).status_code == 422


# ── Activación de cuentas creadas por un administrador ────────────────────


class _Transporte:
    nombre = "doble"

    def __init__(self) -> None:
        self.enviados = []

    async def enviar(self, mensaje):
        self.enviados.append(mensaje)


def _ajustes_bienvenida() -> Settings:
    return Settings(
        environment="test", _env_file=None, jwt_secret_key="x" * 48, frontend_url="https://coldcare.example",
        email_from="coldcare@prueba.pe",
    )


async def test_alta_por_admin_envia_codigo_de_activacion_y_permite_elegir_password(
    client, app, token_admin, db_session_factory
):
    transporte = _Transporte()
    app.dependency_overrides[get_notificador_bienvenida] = lambda: NotificadorBienvenida(
        _ajustes_bienvenida(), transporte
    )
    temporal = "Clave-Temporal-2026x"
    alta = client.post(
        "/api/usuarios",
        json={"nombre": "Ana Prueba", "email": "ana@prueba.pe", "password": temporal, "rol": "farmaceutico"},
        headers=auth_header(token_admin),
    )
    assert alta.status_code == 201 and alta.json()["debe_cambiar_password"] is True

    (correo,) = transporte.enviados
    assert correo.asunto == "[ColdCare] Tu cuenta fue creada"
    assert temporal not in correo.cuerpo and temporal not in correo.html  # nunca una contraseña
    assert "https://coldcare.example/recuperar-contrasena?email=ana%40prueba.pe&modo=activar" in correo.cuerpo
    assert "48 horas" in correo.cuerpo
    codigo = re.search(r"^\s+(\d{6})$", correo.cuerpo, re.MULTILINE).group(1)
    assert codigo in correo.html and codigo not in correo.asunto

    async with db_session_factory() as session:
        (fila,) = (await session.execute(select(CodigoVerificacionModel))).scalars().all()
    assert fila.proposito == "activacion"
    assert timedelta(hours=47) < fila.expira_en.replace(tzinfo=timezone.utc) - datetime.now(tz=timezone.utc)

    assert _restablecer_con_codigo(client, codigo, email="ANA@prueba.pe").status_code == 204
    login = _login(client, "ana@prueba.pe", NUEVA)
    assert login.status_code == 200 and login.json()["require_password_change"] is False

    acciones = await _acciones(db_session_factory)
    bienvenida = next(a for a in acciones if a.accion == "BIENVENIDA_NOTIFICADA")
    assert bienvenida.detalle["codigo_activacion"] is True
    restablecida = next(a for a in acciones if a.accion == "PASSWORD_RESTABLECIDA")
    assert restablecida.detalle["proposito"] == "activacion"


async def test_sin_correo_el_alta_no_emite_codigo(client, app, token_admin, db_session_factory):
    ajustes = Settings(environment="test", _env_file=None, jwt_secret_key="x" * 48)
    app.dependency_overrides[get_notificador_bienvenida] = lambda: NotificadorBienvenida(ajustes)
    alta = client.post(
        "/api/usuarios",
        json={"nombre": "Ana", "email": "ana@prueba.pe", "password": "Clave-Temporal-2026x", "rol": "tecnico"},
        headers=auth_header(token_admin),
    )
    assert alta.status_code == 201
    async with db_session_factory() as session:
        assert (await session.execute(select(CodigoVerificacionModel))).scalars().all() == []


async def test_el_alta_rechaza_un_correo_que_solo_difiere_en_mayusculas(client, app, token_admin):
    app.dependency_overrides[get_notificador_bienvenida] = lambda: NotificadorBienvenida(
        Settings(environment="test", _env_file=None, jwt_secret_key="x" * 48)
    )
    cuerpo = {"nombre": "Admin 2", "email": "ADMIN@farmacia.example.org", "password": "Clave-2026xyz", "rol": "tecnico"}
    assert client.post("/api/usuarios", json=cuerpo, headers=auth_header(token_admin)).status_code == 409


async def test_crear_usuario_con_fixture_sigue_igual(crear_usuario):
    usuario = await crear_usuario("Ana", "ana@x.pe", PASSWORD, Rol.TECNICO)
    assert usuario.email == "ana@x.pe"


async def test_produccion_sin_correo_ni_clave_de_codigos_no_rompe_alta_ni_enlace(client, app, token_admin):
    """En producción sin correo, CODIGOS_HMAC_KEY puede faltar: el alta sigue
    funcionando sin código y los endpoints de código responden 503, no 500."""
    from src.infrastructure.config import get_settings

    produccion = Settings(
        environment="production", _env_file=None, jwt_secret_key=get_settings().jwt_secret_key,
        trazabilidad_hmac_key="h" * 40,
        database_url="postgresql://u:p@db:5432/x", allowed_hosts=["testserver"],
        cors_origins=["https://coldcare.example"], mqtt_enabled=False,
    )
    app.dependency_overrides[get_settings] = lambda: produccion
    app.dependency_overrides[get_notificador_bienvenida] = lambda: NotificadorBienvenida(produccion)

    alta = client.post(
        "/api/usuarios",
        json={"nombre": "Ana", "email": "ana@prueba.pe", "password": "Clave-Temporal-2026x", "rol": "tecnico"},
        headers=auth_header(token_admin),
    )
    assert alta.status_code == 201
    legado = client.post("/api/auth/restablecer-password", json={"token": "t" * 30, "password_nueva": NUEVA})
    assert legado.status_code == 400
    assert _restablecer_con_codigo(client, "123456").status_code == 503
    verificar = client.post("/api/auth/login/verificar", json={"desafio": "d" * 43, "codigo": "123456"})
    assert verificar.status_code == 503


# ── Cualquier cambio de contraseña anula los códigos pendientes ───────────


async def _codigos_vivos(fabrica) -> list[CodigoVerificacionModel]:
    async with fabrica() as session:
        filas = (await session.execute(select(CodigoVerificacionModel))).scalars().all()
    return [f for f in filas if f.usado_en is None]


async def test_cambiar_la_contrasena_anula_el_codigo_de_activacion(client, app, token_admin, db_session_factory):
    """Sin esto, durante 48 h el código de activación seguía pudiendo
    sobrescribir la contraseña que la persona ya había elegido."""
    transporte = _Transporte()
    app.dependency_overrides[get_notificador_bienvenida] = lambda: NotificadorBienvenida(
        _ajustes_bienvenida(), transporte
    )
    temporal = "Clave-Temporal-2026x"
    alta = client.post(
        "/api/usuarios",
        json={"nombre": "Ana Prueba", "email": "ana@prueba.pe", "password": temporal, "rol": "tecnico"},
        headers=auth_header(token_admin),
    )
    assert alta.status_code == 201
    codigo = re.search(r"^\s+(\d{6})$", transporte.enviados[0].cuerpo, re.MULTILINE).group(1)
    token = _login(client, "ana@prueba.pe", temporal).json()["access_token"]

    cambio = client.post(
        "/api/auth/cambiar-password",
        json={"password_actual": temporal, "password_nueva": NUEVA},
        headers=auth_header(token),
    )
    assert cambio.status_code == 200

    assert await _codigos_vivos(db_session_factory) == []
    assert _restablecer_con_codigo(client, codigo, email="ana@prueba.pe", nueva="Tercera-clave-9").status_code == 400
    assert _login(client, "ana@prueba.pe", NUEVA).status_code == 200


async def test_el_restablecimiento_por_admin_anula_los_codigos_pendientes(
    client, token_tecnico, token_admin, correo, db_session_factory
):
    _solicitar(client)
    codigo = correo.ultimo_codigo()
    async with db_session_factory() as session:
        tecnico = await SQLAlchemyUsuarioRepository(session).obtener_por_email(TECNICO)

    respuesta = client.post(f"/api/usuarios/{tecnico.id}/restablecer-password", headers=auth_header(token_admin))
    assert respuesta.status_code == 200

    # El administrador borra el historial (y con él el tope de fallos).
    async with db_session_factory() as session:
        assert (await session.execute(select(CodigoVerificacionModel))).scalars().all() == []
    assert _restablecer_con_codigo(client, codigo).status_code == 400


async def test_la_reactivacion_por_admin_anula_los_codigos_pendientes(
    client, token_tecnico, token_admin, correo, db_session_factory
):
    _solicitar(client)
    codigo = correo.ultimo_codigo()
    async with db_session_factory() as session:
        tecnico = await SQLAlchemyUsuarioRepository(session).obtener_por_email(TECNICO)
    baja = client.patch(
        f"/api/usuarios/{tecnico.id}/desactivar", json={"motivo": "renuncia"}, headers=auth_header(token_admin)
    )
    assert baja.status_code == 200

    assert client.post(f"/api/usuarios/{tecnico.id}/reactivar", headers=auth_header(token_admin)).status_code == 200
    async with db_session_factory() as session:
        assert (await session.execute(select(CodigoVerificacionModel))).scalars().all() == []
    assert _restablecer_con_codigo(client, codigo).status_code == 400


# ── Casos negativos del restablecimiento por código ───────────────────────


async def test_un_codigo_de_login_no_sirve_para_restablecer(client, token_tecnico, correo, db_session_factory):
    async with db_session_factory() as session:
        tecnico = await SQLAlchemyUsuarioRepository(session).obtener_por_email(TECNICO)
        servicio = ServicioCodigosVerificacion(
            SQLAlchemyCodigoVerificacionRepository(session), get_settings().clave_codigos, PoliticaCodigos()
        )
        emitido = await servicio.emitir(tecnico.id, PropositoCodigo.LOGIN, con_desafio=True)
        await session.commit()

    assert _restablecer_con_codigo(client, emitido.codigo).status_code == 400
    assert _login(client, TECNICO, PASSWORD).status_code == 200


async def test_una_cuenta_desactivada_no_puede_restablecer_con_su_codigo(
    client, token_tecnico, correo, db_session_factory
):
    _solicitar(client)
    async with db_session_factory() as session:
        await session.execute(update(UserModel).where(UserModel.email == TECNICO).values(is_active=False))
        await session.commit()

    assert _restablecer_con_codigo(client, correo.ultimo_codigo()).status_code == 400


async def test_el_codigo_de_una_cuenta_no_sirve_con_el_correo_de_otra(
    client, token_tecnico, token_farmaceutico, correo
):
    _solicitar(client)

    otra = _restablecer_con_codigo(client, correo.ultimo_codigo(), email="farmaceutico@farmacia.example.org")
    assert otra.status_code == 400
    assert _login(client, "farmaceutico@farmacia.example.org", PASSWORD).status_code == 200
    assert _restablecer_con_codigo(client, correo.ultimo_codigo()).status_code == 204  # sigue siendo del técnico


# ── Fuerza bruta entre códigos ────────────────────────────────────────────


def _sin_cuota_por_ip(app) -> None:
    """La cuota por IP de restablecer-password (10 cada 15 min) coincide con la
    de la cuenta: se apaga para probar la de la cuenta por separado."""
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"api_rate_limit_habilitado": False}
    )


async def test_diez_fallos_entre_varios_codigos_bloquean_la_cuenta(client, app, token_tecnico, correo):
    """Cada código se quema a los 5 fallos, pero pedir códigos nuevos no da
    intentos nuevos: la cuota por cuenta (compartida con el login) corta a los 10."""
    _sin_cuota_por_ip(app)
    for _ in range(2):
        _solicitar(client)
        codigo = correo.ultimo_codigo()
        for _ in range(5):
            assert _restablecer_con_codigo(client, "000000" if codigo != "000000" else "111111").status_code == 400
    _solicitar(client)  # tercer código (la cuota de solicitudes es 3 cada 15 min)

    bloqueado = _restablecer_con_codigo(client, correo.ultimo_codigo())  # incluso con el código correcto
    assert bloqueado.status_code == 429 and int(bloqueado.headers["Retry-After"]) > 0
    assert bloqueado.json()["detail"].startswith("Demasiados intentos fallidos")  # la de la cuenta, no la de IP
    # La cuota es la misma del login: la cuenta tampoco puede iniciar sesión por ahora.
    assert _login(client, TECNICO, PASSWORD).status_code == 429
    assert len(correo.enviados) == 3
    app.state.login_cuenta_rate_limiter.reiniciar(TECNICO)
    assert _login(client, TECNICO, PASSWORD).status_code == 200  # la contraseña no cambió


async def test_un_restablecimiento_correcto_reinicia_la_cuota_por_cuenta(client, app, token_tecnico, correo):
    _sin_cuota_por_ip(app)
    _solicitar(client)
    for _ in range(5):
        _restablecer_con_codigo(client, "000000" if correo.ultimo_codigo() != "000000" else "111111")
    _solicitar(client)
    codigo = correo.ultimo_codigo()
    for _ in range(4):
        _restablecer_con_codigo(client, "000000" if codigo != "000000" else "111111")
    assert _restablecer_con_codigo(client, codigo).status_code == 204  # 9 fallos y luego el correcto

    # Sin el reinicio, el segundo de estos intentos ya sería el undécimo.
    assert [_restablecer_con_codigo(client, "123456").status_code for _ in range(9)] == [400] * 9


async def test_el_tope_en_la_base_sobrevive_a_un_reinicio_de_los_limitadores(client, app, token_tecnico, correo):
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(update={"codigo_max_fallos_cuenta": 5})
    _solicitar(client)
    codigo = correo.ultimo_codigo()
    for _ in range(5):
        _restablecer_con_codigo(client, "000000" if codigo != "000000" else "111111")
    for limitador in ("login_cuenta_rate_limiter", "recuperacion_cuenta_rate_limiter"):
        getattr(app.state, limitador).reiniciar(TECNICO)

    assert _solicitar(client).status_code == 202
    assert len(correo.enviados) == 1  # misma respuesta, pero ningún código nuevo
    assert _login(client, TECNICO, PASSWORD).status_code == 200  # la contraseña no cambió


# ── Correos heredados que solo difieren en mayúsculas ─────────────────────


async def test_entre_filas_que_difieren_en_mayusculas_gana_la_coincidencia_exacta(
    client, crear_usuario, correo, db_session_factory
):
    """El repositorio prefiere la fila escrita exactamente igual; los llamadores
    no deben pasar el correo a minúsculas antes de buscarlo."""
    a = await crear_usuario("Ana A", "Ana@X.pe", "Clave-de-A-2026", Rol.TECNICO)
    _esperar_al_siguiente_segundo()  # B es posterior (created_at decide el desempate)
    b = await crear_usuario("Ana B", "ana@x.pe", "Clave-de-B-2026", Rol.TECNICO)
    async with db_session_factory() as session:
        repo = SQLAlchemyUsuarioRepository(session)
        assert (await repo.obtener_por_email("Ana@X.pe")).id == a.id
        assert (await repo.obtener_por_email("ana@x.pe")).id == b.id
        assert (await repo.obtener_por_email("ANA@x.pe")).id == a.id  # ninguna exacta: la más antigua

    assert _login(client, "Ana@X.pe", "Clave-de-A-2026").status_code == 200
    assert _login(client, "ana@x.pe", "Clave-de-B-2026").status_code == 200
    _solicitar(client, "Ana@X.pe")
    assert correo.enviados[-1][0] == "Ana@X.pe"
    assert _restablecer_con_codigo(client, correo.ultimo_codigo(), email="Ana@X.pe").status_code == 204
    assert _login(client, "Ana@X.pe", NUEVA).status_code == 200
    assert _login(client, "ana@x.pe", "Clave-de-B-2026").status_code == 200  # B intacta


async def test_un_fallo_en_segundo_plano_no_cambia_la_respuesta_ni_filtra_el_correo(
    client, token_tecnico, correo, monkeypatch
):
    """La cuenta se busca después de responder: si eso falla, el cliente ya
    recibió el mismo 202 y el log no lleva el correo."""
    from src.application.use_cases import gestionar_credenciales
    from src.interface.api import auth_router

    async def _falla(self, email):
        raise RuntimeError(f"fallo con {email}")

    registrados: list[str] = []
    monkeypatch.setattr(gestionar_credenciales.SolicitarRecuperacionPasswordUseCase, "execute", _falla)
    monkeypatch.setattr(auth_router.logger, "error", lambda msg, *a, **k: registrados.append(msg % a if a else msg))

    respuesta = _solicitar(client)
    assert respuesta.status_code == 202
    assert correo.enviados == []
    assert registrados and all(TECNICO not in r for r in registrados)
