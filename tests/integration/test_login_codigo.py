"""Segundo factor del login con contraseña: código de 6 dígitos por correo (OTP_LOGIN_ENABLED)."""

import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from src.domain.value_objects.rol import Rol
from src.infrastructure.config import Settings, get_settings
from src.infrastructure.database.models import AuditLogModel, CodigoVerificacionModel
from src.interface.api import deps
from tests.conftest import auth_header

EMAIL = "tecnico@farmacia.example.org"
PASSWORD = "password123"


class _CorreoFalso:
    def __init__(self, configurado: bool = True, falla: bool = False) -> None:
        self.configurado = configurado
        self._falla = falla
        self.enviados: list[tuple[str, str, str, str | None]] = []

    async def enviar(self, destino: str, asunto: str, cuerpo: str, html: str | None = None) -> bool:
        if self._falla:
            return False
        self.enviados.append((destino, asunto, cuerpo, html))
        return True

    def ultimo_codigo(self) -> str:
        return re.search(r"\b(\d{6})\b", self.enviados[-1][2]).group(1)


def _ajustes(**extra) -> Settings:
    base = {"environment": "test", "_env_file": None, "jwt_secret_key": "x" * 48, "otp_login_enabled": True}
    base.update(extra)
    return Settings(**base)


@pytest.fixture
def correo(app):
    falso = _CorreoFalso()
    app.dependency_overrides[deps.get_enviador_correo] = lambda: falso
    return falso


@pytest.fixture
def otp(app, correo):
    app.dependency_overrides[get_settings] = lambda: _ajustes()
    return correo


@pytest.fixture
async def tecnico(crear_usuario):
    return await crear_usuario("Tecnico Test", EMAIL, PASSWORD, Rol.TECNICO)


def _login(client, email: str = EMAIL, password: str = PASSWORD):
    return client.post("/api/auth/login", data={"username": email, "password": password})


def _verificar(client, desafio: str, codigo: str):
    return client.post("/api/auth/login/verificar", json={"desafio": desafio, "codigo": codigo})


def _incorrecto(codigo: str) -> str:
    return "000000" if codigo != "000000" else "111111"


async def _acciones(fabrica) -> list[AuditLogModel]:
    async with fabrica() as session:
        return list((await session.execute(select(AuditLogModel).order_by(AuditLogModel.created_at))).scalars().all())


async def _codigos(fabrica) -> list[CodigoVerificacionModel]:
    async with fabrica() as session:
        return list((await session.execute(select(CodigoVerificacionModel))).scalars().all())


async def _envejecer(fabrica, **valores) -> None:
    async with fabrica() as session:
        await session.execute(update(CodigoVerificacionModel).values(**valores))
        await session.commit()


# ── OTP deshabilitado: exactamente el comportamiento anterior ──────────────


async def test_sin_otp_el_login_devuelve_el_token_como_antes(client, tecnico, correo, db_session_factory):
    respuesta = _login(client)

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["access_token"] and cuerpo["token_type"] == "bearer" and cuerpo["requiere_codigo"] is False
    assert cuerpo["desafio"] is None
    assert correo.enviados == [] and await _codigos(db_session_factory) == []
    exitoso = [a for a in await _acciones(db_session_factory) if a.accion == "LOGIN_EXITOSO"]
    assert exitoso[-1].detalle == {"metodo": "password"}


async def test_sin_otp_el_correo_no_distingue_mayusculas(client, tecnico):
    assert _login(client, "  TECNICO@Farmacia.Example.ORG ").status_code == 200


# ── OTP habilitado ────────────────────────────────────────────────────────


async def test_login_con_codigo_camino_feliz(client, tecnico, otp, db_session_factory):
    respuesta = _login(client)

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["access_token"] is None and cuerpo["requiere_codigo"] is True
    assert cuerpo["email_enmascarado"] == "t***@farmacia.example.org"
    assert cuerpo["expira_en_segundos"] == 600 and cuerpo["reenviar_en_segundos"] == 60
    desafio = cuerpo["desafio"]
    assert len(desafio) >= 40

    (destino, asunto, texto, html) = otp.enviados[0]
    codigo = otp.ultimo_codigo()
    assert destino == EMAIL and asunto == "[ColdCare] Tu código para iniciar sesión"
    assert codigo not in asunto and codigo in html and "no fuiste t" in texto.lower()
    # La base no guarda ni el código ni el desafío.
    (fila,) = await _codigos(db_session_factory)
    assert codigo not in fila.codigo_hash and fila.desafio_hash != desafio

    sesion = _verificar(client, desafio, codigo)
    assert sesion.status_code == 200
    token = sesion.json()["access_token"]
    assert token and sesion.json()["require_password_change"] is False
    assert sesion.json()["require_privacy_consent"] is False
    assert client.get("/api/alertas", headers=auth_header(token)).status_code == 200

    acciones = await _acciones(db_session_factory)
    assert [a.accion for a in acciones][-2:] == ["LOGIN_CODIGO_EMITIDO", "LOGIN_EXITOSO"]
    assert acciones[-1].detalle == {"metodo": "password+codigo"}
    assert all(codigo not in str(a.detalle) and desafio not in str(a.detalle) for a in acciones)


async def test_el_codigo_no_se_puede_reutilizar(client, tecnico, otp):
    desafio = _login(client).json()["desafio"]
    codigo = otp.ultimo_codigo()

    assert _verificar(client, desafio, codigo).status_code == 200
    segunda = _verificar(client, desafio, codigo)
    assert segunda.status_code == 401
    assert "Vuelve a iniciar sesión" in segunda.json()["detail"]


async def test_cinco_codigos_incorrectos_queman_el_desafio(client, tecnico, otp, db_session_factory):
    desafio = _login(client).json()["desafio"]
    codigo = otp.ultimo_codigo()

    detalles = [_verificar(client, desafio, _incorrecto(codigo)).json()["detail"] for _ in range(5)]
    assert detalles[:4] == ["El código no es correcto."] * 4
    assert "agotaron" in detalles[4]

    tarde = _verificar(client, desafio, codigo)
    assert tarde.status_code == 401
    (fila,) = await _codigos(db_session_factory)
    assert fila.intentos == 5 and fila.usado_en is not None
    fallidos = [a for a in await _acciones(db_session_factory) if a.accion == "LOGIN_CODIGO_FALLIDO"]
    assert len(fallidos) == 6 and fallidos[0].detalle["intentos_restantes"] == 4


async def test_codigo_caducado_se_rechaza(client, tecnico, otp, db_session_factory):
    desafio = _login(client).json()["desafio"]
    await _envejecer(db_session_factory, expira_en=datetime.now(tz=timezone.utc) - timedelta(seconds=1))

    respuesta = _verificar(client, desafio, otp.ultimo_codigo())
    assert respuesta.status_code == 401
    fallido = [a for a in await _acciones(db_session_factory) if a.accion == "LOGIN_CODIGO_FALLIDO"][-1]
    assert fallido.detalle["motivo"] == "caducado"


async def test_desafio_inventado_se_rechaza_igual(client, tecnico, otp):
    respuesta = _verificar(client, "desafio-que-no-existe-0123456789abcdef", "123456")
    assert respuesta.status_code == 401
    assert respuesta.json()["detail"] == "La verificación caducó o no es válida. Vuelve a iniciar sesión."


async def test_un_nuevo_login_invalida_el_codigo_anterior(client, tecnico, otp):
    primero = _login(client).json()["desafio"]
    codigo_viejo = otp.ultimo_codigo()
    _login(client)

    assert _verificar(client, primero, codigo_viejo).status_code == 401


async def test_reenviar_respeta_la_espera_y_luego_envia_otro_codigo(client, tecnico, otp, db_session_factory):
    desafio = _login(client).json()["desafio"]
    primero = otp.ultimo_codigo()

    pronto = client.post("/api/auth/login/reenviar", json={"desafio": desafio})
    assert pronto.status_code == 429
    assert 1 <= int(pronto.headers["Retry-After"]) <= 60
    assert len(otp.enviados) == 1

    await _envejecer(db_session_factory, creado_en=datetime.now(tz=timezone.utc) - timedelta(seconds=61))
    reenvio = client.post("/api/auth/login/reenviar", json={"desafio": desafio})
    assert reenvio.status_code == 200
    assert reenvio.json()["desafio"] == desafio and reenvio.json()["requiere_codigo"] is True
    assert len(otp.enviados) == 2
    nuevo = otp.ultimo_codigo()
    if nuevo != primero:
        assert _verificar(client, desafio, primero).status_code == 401
    assert _verificar(client, desafio, nuevo).status_code == 200
    assert "LOGIN_CODIGO_REENVIADO" in {a.accion for a in await _acciones(db_session_factory)}


async def test_reenviar_un_desafio_quemado_obliga_a_iniciar_sesion(client, tecnico, otp, db_session_factory):
    desafio = _login(client).json()["desafio"]
    for _ in range(5):
        _verificar(client, desafio, _incorrecto(otp.ultimo_codigo()))
    await _envejecer(db_session_factory, creado_en=datetime.now(tz=timezone.utc) - timedelta(seconds=61))

    assert client.post("/api/auth/login/reenviar", json={"desafio": desafio}).status_code == 401


async def test_el_correo_del_login_no_distingue_mayusculas(client, tecnico, otp):
    respuesta = _login(client, "  Tecnico@FARMACIA.example.org ")
    assert respuesta.status_code == 200 and respuesta.json()["requiere_codigo"] is True
    assert _verificar(client, respuesta.json()["desafio"], otp.ultimo_codigo()).status_code == 200


async def test_password_incorrecta_no_envia_codigo_y_se_audita(client, tecnico, otp, db_session_factory):
    respuesta = _login(client, password="incorrecta-1")

    assert respuesta.status_code == 401 and otp.enviados == []
    assert "LOGIN_FALLIDO" in {a.accion for a in await _acciones(db_session_factory)}


async def test_sin_correo_el_login_con_otp_falla_cerrado(client, app, tecnico):
    app.dependency_overrides[get_settings] = lambda: _ajustes()
    app.dependency_overrides[deps.get_enviador_correo] = lambda: _CorreoFalso(configurado=False)

    respuesta = _login(client)
    assert respuesta.status_code == 503
    assert "correo" in respuesta.json()["detail"]
    # Mismo 503 con contraseña incorrecta: no revela si era correcta.
    assert _login(client, password="incorrecta-1").status_code == 503


async def test_si_el_correo_no_sale_responde_503(client, app, tecnico):
    app.dependency_overrides[get_settings] = lambda: _ajustes()
    app.dependency_overrides[deps.get_enviador_correo] = lambda: _CorreoFalso(falla=True)

    respuesta = _login(client)
    assert respuesta.status_code == 503 and "código" in respuesta.json()["detail"]


async def test_usuario_desactivado_tras_el_codigo_no_entra(client, tecnico, otp, db_session_factory):
    from src.infrastructure.database.models import UserModel

    desafio = _login(client).json()["desafio"]
    async with db_session_factory() as session:
        await session.execute(update(UserModel).values(is_active=False))
        await session.commit()

    assert _verificar(client, desafio, otp.ultimo_codigo()).status_code == 401


async def test_fallos_de_codigo_cuentan_en_la_cuota_por_cuenta(client, app, tecnico, otp):
    """Diez fallos de código en 15 min (varios desafíos) bloquean la cuenta: el
    límite por código no basta si se piden códigos nuevos sin parar."""
    for fallos in (5, 4, 1):  # 10 fallos repartidos en tres desafíos
        desafio = _login(client).json()["desafio"]
        for _ in range(fallos):
            assert _verificar(client, desafio, _incorrecto(otp.ultimo_codigo())).status_code == 401

    # Ni el código correcto del último desafío ni un login nuevo pasan.
    bloqueado = _verificar(client, desafio, otp.ultimo_codigo())
    assert bloqueado.status_code == 429 and "Retry-After" in bloqueado.headers
    assert _login(client).status_code == 429


async def test_la_respuesta_de_verificar_rechaza_codigos_mal_formados(client, tecnico, otp):
    desafio = _login(client).json()["desafio"]
    assert _verificar(client, desafio, "abc123").status_code == 422
    assert client.post("/api/auth/login/verificar", json={"desafio": desafio}).status_code == 422


# ── Cuota de códigos emitidos por cuenta (correo del titular) ─────────────


def _reenviar(client, desafio: str):
    return client.post("/api/auth/login/reenviar", json={"desafio": desafio})


async def test_la_novena_emision_de_codigo_en_15_minutos_responde_429(client, tecnico, otp):
    for _ in range(8):
        respuesta = _login(client)
        assert respuesta.status_code == 200 and respuesta.json()["requiere_codigo"] is True
    assert len(otp.enviados) == 8

    novena = _login(client)
    assert novena.status_code == 429 and int(novena.headers["Retry-After"]) > 0
    assert len(otp.enviados) == 8  # no salió otro correo


async def test_los_reenvios_cuentan_en_la_misma_cuota(client, tecnico, otp, db_session_factory):
    for _ in range(5):
        desafio = _login(client).json()["desafio"]
    for _ in range(3):
        await _envejecer(db_session_factory, creado_en=datetime.now(tz=timezone.utc) - timedelta(seconds=61))
        assert _reenviar(client, desafio).status_code == 200
    assert len(otp.enviados) == 8

    await _envejecer(db_session_factory, creado_en=datetime.now(tz=timezone.utc) - timedelta(seconds=61))
    reenvio = _reenviar(client, desafio)
    assert reenvio.status_code == 429 and "Retry-After" in reenvio.headers
    assert _login(client).status_code == 429
    assert len(otp.enviados) == 8


async def test_un_login_verificado_reinicia_la_cuota_de_emision(client, tecnico, otp):
    for _ in range(7):
        desafio = _login(client).json()["desafio"]
    assert _verificar(client, desafio, otp.ultimo_codigo()).status_code == 200

    # Sin el reinicio, el segundo de estos logins sería la novena emisión.
    assert all(_login(client).status_code == 200 for _ in range(8))


# ── El desafío no sobrevive a un cambio de contraseña ni se alarga sin fin ─


async def test_un_desafio_pendiente_muere_al_cambiar_la_contrasena(client, tecnico, otp, db_session_factory):
    desafio = _login(client).json()["desafio"]
    token = _verificar(client, desafio, otp.ultimo_codigo()).json()["access_token"]

    pendiente = _login(client).json()["desafio"]  # login empezado con la contraseña actual
    codigo = otp.ultimo_codigo()
    cambio = client.post(
        "/api/auth/cambiar-password",
        json={"password_actual": PASSWORD, "password_nueva": "Clave-Nueva-2026x"},
        headers=auth_header(token),
    )
    assert cambio.status_code == 200

    assert _verificar(client, pendiente, codigo).status_code == 401
    assert all(c.usado_en is not None for c in await _codigos(db_session_factory))


async def test_un_desafio_emitido_antes_de_invalidar_las_sesiones_no_abre_sesion(
    client, tecnico, otp, db_session_factory
):
    """Aunque el código siguiera vivo, un cierre de sesiones posterior a la
    emisión del desafío (cambio de contraseña, rol, desactivación) lo invalida."""
    from src.infrastructure.database.models import UserModel

    desafio = _login(client).json()["desafio"]
    async with db_session_factory() as session:
        await session.execute(
            update(UserModel).values(tokens_invalidos_antes_de=datetime.now(tz=timezone.utc) + timedelta(seconds=1))
        )
        await session.commit()
    await _envejecer(db_session_factory, creado_en=datetime.now(tz=timezone.utc) - timedelta(seconds=61))

    assert _reenviar(client, desafio).status_code == 401
    assert _verificar(client, desafio, otp.ultimo_codigo()).status_code == 401
    assert all(c.usado_en is not None for c in await _codigos(db_session_factory))


async def test_reenviar_no_alarga_el_desafio_indefinidamente(client, tecnico, otp, db_session_factory):
    desafio = _login(client).json()["desafio"]
    ahora = datetime.now(tz=timezone.utc)
    # Reenviado una y otra vez durante 21 minutos: pasó el doble de su vigencia.
    await _envejecer(db_session_factory, emitido_en=ahora - timedelta(minutes=21), creado_en=ahora - timedelta(seconds=61))

    respuesta = _reenviar(client, desafio)
    assert respuesta.status_code == 401 and "Vuelve a iniciar sesión" in respuesta.json()["detail"]
    assert len(otp.enviados) == 1


async def test_reenviar_un_desafio_caducado_responde_401(client, tecnico, otp, db_session_factory):
    desafio = _login(client).json()["desafio"]
    ahora = datetime.now(tz=timezone.utc)
    await _envejecer(db_session_factory, expira_en=ahora - timedelta(seconds=1), creado_en=ahora - timedelta(seconds=61))

    assert _reenviar(client, desafio).status_code == 401
    assert len(otp.enviados) == 1


async def test_reenviar_sin_correo_configurado_responde_503(client, app, tecnico, otp):
    desafio = _login(client).json()["desafio"]
    app.dependency_overrides[deps.get_enviador_correo] = lambda: _CorreoFalso(configurado=False)

    respuesta = _reenviar(client, desafio)
    assert respuesta.status_code == 503 and "correo" in respuesta.json()["detail"]


# ── Tope de fallos por cuenta persistido en la base ───────────────────────


async def test_el_tope_de_fallos_sobrevive_a_un_reinicio_de_los_limitadores(client, app, tecnico, otp):
    """Los limitadores en memoria se vacían con cada despliegue: el tope que
    vive en la base sigue impidiendo pedir códigos nuevos para seguir probando."""
    app.dependency_overrides[get_settings] = lambda: _ajustes(codigo_max_fallos_cuenta=5)
    desafio = _login(client).json()["desafio"]
    for _ in range(5):
        _verificar(client, desafio, _incorrecto(otp.ultimo_codigo()))
    for limitador in ("login_cuenta_rate_limiter", "codigo_login_emision_rate_limiter", "login_rate_limiter"):
        getattr(app.state, limitador).reiniciar(EMAIL)
        getattr(app.state, limitador).reiniciar("testclient")

    bloqueado = _login(client)
    assert bloqueado.status_code == 429 and int(bloqueado.headers["Retry-After"]) == 24 * 3600
    assert len(otp.enviados) == 1
