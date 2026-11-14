"""Códigos de verificación de un solo uso: huella, vigencia, intentos, reenvío,
consumo atómico y tope de fallos por cuenta."""

import hashlib
import hmac
import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.application.use_cases.codigos_verificacion import (
    EstadoVerificacion,
    PoliticaCodigos,
    ServicioCodigosVerificacion,
    generar_codigo,
    huella_codigo,
    huella_desafio,
    normalizar_codigo,
)
from src.domain.entities.codigo_verificacion import CodigoVerificacion, PropositoCodigo
from src.domain.exceptions import CodigosBloqueadosError, ReenvioPrematuroError
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.models import CodigoVerificacionModel
from src.infrastructure.database.repositories.codigo_verificacion_repository import (
    SQLAlchemyCodigoVerificacionRepository,
)

CLAVE = b"clave-hmac-de-codigos-para-pruebas-0123456789"
LOGIN, RECUPERACION, ACTIVACION = PropositoCodigo.LOGIN, PropositoCodigo.RECUPERACION, PropositoCodigo.ACTIVACION


@pytest.fixture
async def usuario(crear_usuario):
    return await crear_usuario("Ana", "ana@farmacia.example.org", "password123", Rol.FARMACEUTICO)


class Reloj:
    """Reloj fijo que solo avanza cuando la prueba lo pide."""

    def __init__(self) -> None:
        self.ahora = datetime.now(tz=timezone.utc).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.ahora

    def avanzar(self, **delta) -> None:
        self.ahora += timedelta(**delta)


def _servicio(session, reloj: Reloj | None = None, repo=None, **politica) -> ServicioCodigosVerificacion:
    extra = {"reloj": reloj} if reloj is not None else {}
    return ServicioCodigosVerificacion(
        repo or SQLAlchemyCodigoVerificacionRepository(session), CLAVE, PoliticaCodigos(**politica), **extra
    )


def _otro(codigo: str) -> str:
    return "000000" if codigo != "000000" else "111111"


async def _filas(fabrica) -> list[CodigoVerificacionModel]:
    async with fabrica() as session:
        return list((await session.execute(select(CodigoVerificacionModel))).scalars().all())


def test_el_codigo_son_seis_digitos_con_ceros_a_la_izquierda(monkeypatch):
    from src.application.use_cases import codigos_verificacion

    assert all(re.fullmatch(r"\d{6}", generar_codigo()) for _ in range(200))
    monkeypatch.setattr(codigos_verificacion.secrets, "randbelow", lambda n: 42)
    assert generar_codigo() == "000042"


def test_la_huella_es_hmac_con_clave_ligada_a_usuario_y_proposito():
    import uuid

    uid = uuid.uuid4()
    huella = huella_codigo(CLAVE, uid, LOGIN, "123456")
    assert huella == hmac.new(CLAVE, f"{uid}:login:123456".encode(), hashlib.sha256).hexdigest()
    assert huella != hashlib.sha256(b"123456").hexdigest()  # no es un SHA-256 sin clave
    assert huella != huella_codigo(b"otra-clave", uid, LOGIN, "123456")
    assert huella != huella_codigo(CLAVE, uid, RECUPERACION, "123456")
    assert huella != huella_codigo(CLAVE, uuid.uuid4(), LOGIN, "123456")
    assert normalizar_codigo(" 123 - 456 ") == "123456"


async def test_emitir_guarda_solo_huellas(db_session_factory, usuario):
    async with db_session_factory() as session:
        emitido = await _servicio(session).emitir(usuario.id, LOGIN, ip="10.0.0.1", con_desafio=True)
        await session.commit()

    (fila,) = await _filas(db_session_factory)
    assert emitido.codigo not in (fila.codigo_hash, fila.desafio_hash)
    assert fila.codigo_hash == huella_codigo(CLAVE, usuario.id, LOGIN, emitido.codigo)
    assert fila.desafio_hash == huella_desafio(emitido.desafio) and len(emitido.desafio) >= 40
    assert (fila.intentos, fila.usado_en, fila.ip, fila.proposito) == (0, None, "10.0.0.1", "login")
    assert emitido.vigencia_segundos == 600


async def test_activacion_dura_48_horas_y_login_10_minutos(db_session_factory, usuario):
    async with db_session_factory() as session:
        servicio = _servicio(session)
        assert (await servicio.emitir(usuario.id, ACTIVACION)).vigencia == timedelta(hours=48)
        assert (await servicio.emitir(usuario.id, RECUPERACION)).vigencia == timedelta(minutes=10)


async def test_un_codigo_nuevo_anula_el_anterior_del_mismo_grupo(db_session_factory, usuario):
    async with db_session_factory() as session:
        servicio = _servicio(session)
        login = await servicio.emitir(usuario.id, LOGIN, con_desafio=True)
        primero = await servicio.emitir(usuario.id, RECUPERACION)
        segundo = await servicio.emitir(usuario.id, RECUPERACION)
        await session.commit()

        if primero.codigo != segundo.codigo:
            viejo = await servicio.verificar_para_usuario(usuario.id, [RECUPERACION], primero.codigo)
            assert not viejo.valido
        assert (await servicio.verificar_para_usuario(usuario.id, [RECUPERACION], segundo.codigo)).valido
        # Recuperación y activación son el mismo grupo: emitir una anula la otra.
        activacion = await servicio.emitir(usuario.id, ACTIVACION)
        recuperacion = await servicio.emitir(usuario.id, RECUPERACION)
        await session.commit()
        if activacion.codigo != recuperacion.codigo:
            anulada = await servicio.verificar_para_usuario(usuario.id, [RECUPERACION, ACTIVACION], activacion.codigo)
            assert not anulada.valido
        # El login no es del grupo: sigue vivo.
        registro = await servicio.buscar_por_desafio(login.desafio)
        assert (await servicio.verificar_registro(registro, login.codigo)).valido
        await session.commit()

    # Los anulados no se borran (conservan sus intentos para el tope por cuenta).
    filas = await _filas(db_session_factory)
    assert len(filas) == 5 and [f.proposito for f in filas if f.usado_en is None] == ["recuperacion"]


async def test_solo_un_codigo_vivo_por_usuario_y_proposito(db_session_factory, usuario):
    """Índice único parcial: aunque dos emisiones se cruzaran, la base no
    admite dos códigos vivos del mismo propósito."""
    async with db_session_factory() as session:
        await _servicio(session).emitir(usuario.id, RECUPERACION)
        ahora = datetime.now(tz=timezone.utc)
        session.add(
            CodigoVerificacionModel(
                usuario_id=usuario.id, proposito="recuperacion", codigo_hash="x" * 64, expira_en=ahora + timedelta(minutes=5)
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()


async def test_verificar_compara_solo_con_el_codigo_vivo_mas_reciente(db_session_factory, usuario):
    """Si quedaran dos códigos vivos (recuperación y activación emitidos sin el
    servicio), un intento cuenta como una sola apuesta: contra el más reciente."""
    reloj = Reloj()
    async with db_session_factory() as session:
        repo = SQLAlchemyCodigoVerificacionRepository(session)
        servicio = _servicio(session, reloj)
        codigos = {}
        for proposito in (ACTIVACION, RECUPERACION):
            codigos[proposito] = "123456" if proposito == ACTIVACION else "654321"
            await repo.emitir(
                CodigoVerificacion(
                    usuario_id=usuario.id, proposito=proposito,
                    codigo_hash=huella_codigo(CLAVE, usuario.id, proposito, codigos[proposito]),
                    expira_en=reloj() + timedelta(hours=1), creado_en=reloj(), emitido_en=reloj(),
                ),
                anular=[proposito],
                purgar_antes_de=reloj() - timedelta(days=1),
            )
            reloj.avanzar(seconds=1)
        await session.commit()

        viejo = await servicio.verificar_para_usuario(usuario.id, [RECUPERACION, ACTIVACION], codigos[ACTIVACION])
        assert viejo.estado == EstadoVerificacion.INCORRECTO
        await session.commit()
    intentos = {f.proposito: f.intentos for f in await _filas(db_session_factory)}
    assert intentos == {"activacion": 0, "recuperacion": 1}


async def test_codigo_correcto_se_consume_una_sola_vez(db_session_factory, usuario):
    async with db_session_factory() as session:
        servicio = _servicio(session)
        emitido = await servicio.emitir(usuario.id, LOGIN, con_desafio=True)
        registro = await servicio.buscar_por_desafio(emitido.desafio)
        assert (await servicio.verificar_registro(registro, emitido.codigo)).valido
        await session.commit()
        otra_vez = await servicio.verificar_registro(await servicio.buscar_por_desafio(emitido.desafio), emitido.codigo)
        assert otra_vez.estado == EstadoVerificacion.USADO


async def test_cinco_fallos_queman_el_codigo_aunque_luego_llegue_el_correcto(db_session_factory, usuario):
    async with db_session_factory() as session:
        servicio = _servicio(session)
        emitido = await servicio.emitir(usuario.id, LOGIN, con_desafio=True)
        incorrecto = "000000" if emitido.codigo != "000000" else "111111"
        restantes = []
        for _ in range(5):
            registro = await servicio.buscar_por_desafio(emitido.desafio)
            resultado = await servicio.verificar_registro(registro, incorrecto)
            restantes.append((resultado.estado, resultado.intentos_restantes))
        await session.commit()
        assert restantes[:4] == [(EstadoVerificacion.INCORRECTO, n) for n in (4, 3, 2, 1)]
        assert restantes[4] == (EstadoVerificacion.AGOTADO, 0)
        final = await servicio.verificar_registro(await servicio.buscar_por_desafio(emitido.desafio), emitido.codigo)
        assert final.estado == EstadoVerificacion.AGOTADO

    (fila,) = await _filas(db_session_factory)
    assert fila.intentos == 5 and fila.usado_en is not None  # persistido en la base


async def test_codigo_caducado_no_vale(db_session_factory, usuario):
    reloj = Reloj()
    async with db_session_factory() as session:
        servicio = _servicio(session, reloj, ttl=timedelta(minutes=10))
        emitido = await servicio.emitir(usuario.id, RECUPERACION)
        await session.commit()
        reloj.avanzar(minutes=10)  # justo al caducar ya no vale
        resultado = await servicio.verificar_para_usuario(usuario.id, [RECUPERACION], emitido.codigo)
    assert resultado.estado == EstadoVerificacion.INEXISTENTE


async def test_codigo_de_login_caducado_no_vale(db_session_factory, usuario):
    reloj = Reloj()
    async with db_session_factory() as session:
        servicio = _servicio(session, reloj, ttl=timedelta(minutes=10))
        emitido = await servicio.emitir(usuario.id, LOGIN, con_desafio=True)
        reloj.avanzar(minutes=9, seconds=59)
        assert servicio.utilizable(await servicio.buscar_por_desafio(emitido.desafio))
        reloj.avanzar(seconds=1)
        resultado = await servicio.verificar_registro(await servicio.buscar_por_desafio(emitido.desafio), emitido.codigo)
    assert resultado.estado == EstadoVerificacion.CADUCADO


async def test_el_reenvio_respeta_la_espera_y_conserva_desafio_e_intentos(db_session_factory, usuario):
    reloj = Reloj()
    async with db_session_factory() as session:
        servicio = _servicio(session, reloj, reenvio=timedelta(seconds=60))
        emitido = await servicio.emitir(usuario.id, LOGIN, con_desafio=True)
        registro = await servicio.buscar_por_desafio(emitido.desafio)
        await servicio.verificar_registro(registro, _otro(emitido.codigo))
        await session.commit()
        with pytest.raises(ReenvioPrematuroError) as espera:
            await servicio.reenviar(await servicio.buscar_por_desafio(emitido.desafio))
        assert espera.value.segundos == 60

        reloj.avanzar(seconds=59)
        with pytest.raises(ReenvioPrematuroError) as espera:
            await servicio.reenviar(await servicio.buscar_por_desafio(emitido.desafio))
        assert espera.value.segundos == 1

        reloj.avanzar(seconds=2)
        nuevo = await servicio.reenviar(await servicio.buscar_por_desafio(emitido.desafio))
        await session.commit()
        registro = await servicio.buscar_por_desafio(emitido.desafio)
        assert registro.intentos == 1  # reenviar no regala intentos
        assert registro.creado_en.replace(tzinfo=timezone.utc) == reloj()
        if nuevo.codigo != emitido.codigo:
            assert not (await servicio.verificar_registro(registro, emitido.codigo)).valido
            registro = await servicio.buscar_por_desafio(emitido.desafio)
        assert (await servicio.verificar_registro(registro, nuevo.codigo)).valido


async def test_reenviar_no_alarga_el_desafio_mas_alla_del_doble_de_su_vigencia(db_session_factory, usuario):
    reloj = Reloj()
    inicio = reloj()
    async with db_session_factory() as session:
        servicio = _servicio(session, reloj, ttl=timedelta(minutes=10), reenvio=timedelta(seconds=60))
        emitido = await servicio.emitir(usuario.id, LOGIN, con_desafio=True)
        await session.commit()

        reloj.avanzar(minutes=9)
        primero = await servicio.reenviar(await servicio.buscar_por_desafio(emitido.desafio))
        assert primero.registro.expira_en == inicio + timedelta(minutes=19)
        reloj.avanzar(minutes=9)
        segundo = await servicio.reenviar(await servicio.buscar_por_desafio(emitido.desafio))
        # Tope: emisión original + 20 min (el doble de 10), no 18 + 10 min.
        assert segundo.registro.expira_en == inicio + timedelta(minutes=20)
        assert segundo.vigencia == timedelta(minutes=2)
        await session.commit()

        registro = await servicio.buscar_por_desafio(emitido.desafio)
        assert registro.emitido_en.replace(tzinfo=timezone.utc) == inicio  # inmutable
        reloj.avanzar(minutes=2)
        assert not servicio.renovable(registro)
        with pytest.raises(ReenvioPrematuroError):
            await servicio.reenviar(registro)


async def test_consumo_atomico_si_otra_peticion_gana_la_carrera_es_usado(db_session_factory, usuario):
    """Dos peticiones leen el mismo registro vigente; la que llega segunda al
    UPDATE condicional pierde aunque el código sea correcto."""
    async with db_session_factory() as session:
        servicio = _servicio(session)
        emitido = await servicio.emitir(usuario.id, LOGIN, con_desafio=True)
        await session.commit()
        leido = await servicio.buscar_por_desafio(emitido.desafio)  # lectura previa de ambas

        primera = await servicio.verificar_registro(leido, emitido.codigo)
        segunda = await servicio.verificar_registro(leido, emitido.codigo)  # mismo registro ya leído
        assert (primera.estado, segunda.estado) == (EstadoVerificacion.VALIDO, EstadoVerificacion.USADO)


async def test_consumo_atomico_por_correo_si_otra_peticion_gana_la_carrera_es_usado(db_session_factory, usuario):
    class _RepoConCarrera(SQLAlchemyCodigoVerificacionRepository):
        async def listar_utilizables(self, *args, **kwargs):
            vigentes = await super().listar_utilizables(*args, **kwargs)
            for registro in vigentes:  # otra petición los consume entre la lectura y el UPDATE
                await self.consumir(registro.id, registro.codigo_hash, 5, datetime.now(tz=timezone.utc))
            return vigentes

    async with db_session_factory() as session:
        emitido = await _servicio(session).emitir(usuario.id, RECUPERACION)
        servicio = _servicio(session, repo=_RepoConCarrera(session))
        resultado = await servicio.verificar_para_usuario(usuario.id, [RECUPERACION], emitido.codigo)
    assert resultado.estado == EstadoVerificacion.USADO


async def test_anular_pendientes_deja_sin_efecto_los_codigos_sin_borrarlos(db_session_factory, usuario):
    async with db_session_factory() as session:
        servicio = _servicio(session)
        login = await servicio.emitir(usuario.id, LOGIN, con_desafio=True)
        recuperacion = await servicio.emitir(usuario.id, RECUPERACION)
        await servicio.anular_pendientes(usuario.id, [RECUPERACION])
        await session.commit()
        anulado = await servicio.verificar_para_usuario(usuario.id, [RECUPERACION], recuperacion.codigo)
        assert anulado.estado == EstadoVerificacion.INEXISTENTE
        assert (await servicio.verificar_registro(await servicio.buscar_por_desafio(login.desafio), login.codigo)).valido
    assert sorted(f.proposito for f in await _filas(db_session_factory)) == ["login", "recuperacion"]


async def test_reiniciar_borra_el_historial(db_session_factory, usuario):
    async with db_session_factory() as session:
        servicio = _servicio(session)
        await servicio.emitir(usuario.id, LOGIN, con_desafio=True)
        await servicio.emitir(usuario.id, RECUPERACION)
        await servicio.reiniciar(usuario.id, [LOGIN])
        await session.commit()
    assert [f.proposito for f in await _filas(db_session_factory)] == ["recuperacion"]


# ── Tope de fallos por cuenta ─────────────────────────────────────────────


async def _fallar(servicio, usuario_id, emitido, veces: int) -> None:
    for _ in range(veces):
        await servicio.verificar_para_usuario(usuario_id, [RECUPERACION, ACTIVACION], _otro(emitido.codigo))


async def test_pedir_codigos_nuevos_no_da_intentos_nuevos_sin_fin(db_session_factory, usuario):
    """Los fallos de los códigos reemplazados siguen contando: pasado el tope,
    ni se emiten ni se aceptan códigos de ese grupo, aunque sea el correcto."""
    reloj = Reloj()
    async with db_session_factory() as session:
        servicio = _servicio(session, reloj, max_intentos=5, max_fallos_cuenta=12)
        for _ in range(2):  # 2 códigos quemados = 10 fallos
            await _fallar(servicio, usuario.id, await servicio.emitir(usuario.id, RECUPERACION), 5)
        vivo = await servicio.emitir(usuario.id, RECUPERACION)
        await _fallar(servicio, usuario.id, vivo, 1)
        assert not await servicio.bloqueado(usuario.id, RECUPERACION)
        await _fallar(servicio, usuario.id, vivo, 1)  # fallo 12
        await session.commit()

        assert await servicio.bloqueado(usuario.id, ACTIVACION)  # mismo grupo
        assert not await servicio.bloqueado(usuario.id, LOGIN)  # otro grupo
        correcto = await servicio.verificar_para_usuario(usuario.id, [RECUPERACION, ACTIVACION], vivo.codigo)
        assert correcto.estado == EstadoVerificacion.BLOQUEADO
        with pytest.raises(CodigosBloqueadosError) as error:
            await servicio.emitir(usuario.id, RECUPERACION)
        assert error.value.segundos == 24 * 3600
        await servicio.emitir(usuario.id, LOGIN, con_desafio=True)  # el login no se ve afectado

        # Pasada la ventana, los códigos viejos se purgan al emitir y el tope se libera.
        reloj.avanzar(hours=24, seconds=1)
        assert not await servicio.bloqueado(usuario.id, RECUPERACION)
        nuevo = await servicio.emitir(usuario.id, RECUPERACION)
        await session.commit()
        assert (await servicio.verificar_para_usuario(usuario.id, [RECUPERACION], nuevo.codigo)).valido
    assert sum(f.proposito == "recuperacion" for f in await _filas(db_session_factory)) == 1


async def test_el_tope_tambien_bloquea_el_desafio_de_login(db_session_factory, usuario):
    reloj = Reloj()
    async with db_session_factory() as session:
        servicio = _servicio(session, reloj, max_intentos=5, max_fallos_cuenta=5, reenvio=timedelta(0))
        emitido = await servicio.emitir(usuario.id, LOGIN, con_desafio=True)
        for _ in range(4):
            await servicio.verificar_registro(await servicio.buscar_por_desafio(emitido.desafio), _otro(emitido.codigo))
        segundo = await servicio.emitir(usuario.id, LOGIN, con_desafio=True)
        await servicio.verificar_registro(await servicio.buscar_por_desafio(segundo.desafio), _otro(segundo.codigo))
        registro = await servicio.buscar_por_desafio(segundo.desafio)
        resultado = await servicio.verificar_registro(registro, segundo.codigo)
        assert resultado.estado == EstadoVerificacion.BLOQUEADO
        with pytest.raises(CodigosBloqueadosError):
            await servicio.reenviar(registro)
        with pytest.raises(CodigosBloqueadosError):
            await servicio.emitir(usuario.id, LOGIN, con_desafio=True)

        await servicio.reiniciar(usuario.id, [LOGIN])  # cambio de contraseña o administrador
        assert not await servicio.bloqueado(usuario.id, LOGIN)


def test_el_servicio_exige_clave():
    with pytest.raises(ValueError):
        ServicioCodigosVerificacion(None, b"")  # type: ignore[arg-type]
