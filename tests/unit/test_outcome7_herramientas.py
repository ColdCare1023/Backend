"""Outcome 7: herramientas de prueba (cadenas, huecos de seq_no, latencia, SMTP de falla)."""

import asyncio
import smtplib
import socket
from email.message import EmailMessage

import pytest

from scripts.cadenas_prueba import generar_eslabones, verificar_eslabones
from scripts.huecos_seq import analizar
from scripts.informe_latencia import percentil, resumir
from scripts.smtp_falla import servir
from src.infrastructure.config import Settings
from src.interface import main


@pytest.mark.parametrize("version,clave", [(1, None), (2, None), (3, b"clave-de-prueba")])
def test_cadenas_integras_en_cada_formula(version, clave):
    filas = generar_eslabones("PRUEBA", 200, version, clave)
    assert len(filas) == 200 and filas[-1]["chain_seq"] == 200
    assert verificar_eslabones(filas, clave) is None


@pytest.mark.parametrize("version,clave", [(1, None), (2, None), (3, b"clave-de-prueba")])
def test_una_alteracion_se_detecta_en_su_posicion(version, clave):
    filas = generar_eslabones("PRUEBA", 100, version, clave)
    filas[49]["payload"] = {"alterado": True}
    assert verificar_eslabones(filas, clave) == 50


def test_un_eslabon_borrado_rompe_el_enlace():
    filas = generar_eslabones("PRUEBA", 100, 2, None)
    del filas[29]
    assert verificar_eslabones(filas, None) == 31


def test_la_cadena_v3_exige_clave():
    with pytest.raises(ValueError):
        generar_eslabones("PRUEBA", 3, 3, None)


def test_huecos_y_duplicados():
    r = analizar([1, 2, 3, 6, 7, 7, 10])
    assert r["huecos"] == [(4, 5), (8, 9)]
    assert r["faltantes"] == 4
    assert r["duplicados"] == {7: 2}
    assert analizar([5, 6, 7])["faltantes"] == 0
    assert analizar([])["total"] == 0


def test_percentiles_por_rango_mas_cercano():
    datos = [float(i) for i in range(1, 101)]
    r = resumir(datos + [None])
    assert (r["p50_s"], r["p95_s"], r["p99_s"], r["max_s"]) == (50.0, 95.0, 99.0, 100.0)
    assert r["sin_medir"] == 1
    assert percentil([3.0], 99) == 3.0
    with pytest.raises(ValueError):
        percentil([], 50)


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _enviar(puerto: int, timeout: float = 2) -> None:
    correo = EmailMessage()
    correo["From"], correo["To"], correo["Subject"] = "a@x.pe", "b@x.pe", "prueba"
    correo.set_content("hola")
    with smtplib.SMTP("127.0.0.1", puerto, timeout=timeout) as servidor:
        servidor.send_message(correo)


async def _con_servidor(modo: str, accion):
    puerto = _puerto_libre()
    servidor = await servir(modo, "127.0.0.1", puerto)
    try:
        return await asyncio.to_thread(accion, puerto)
    finally:
        servidor.close()
        await servidor.wait_closed()


async def test_smtp_ok_acepta():
    await _con_servidor("ok", _enviar)


@pytest.mark.parametrize(
    "modo,excepcion",
    [
        ("rechazar", smtplib.SMTPConnectError),
        ("temporal", smtplib.SMTPRecipientsRefused),
        ("permanente", smtplib.SMTPRecipientsRefused),
    ],
)
async def test_smtp_de_falla(modo, excepcion):
    with pytest.raises(excepcion):
        await _con_servidor(modo, _enviar)


async def test_smtp_temporal_devuelve_4xx_y_permanente_5xx():
    def codigo(puerto: int) -> int:
        try:
            _enviar(puerto)
        except smtplib.SMTPRecipientsRefused as exc:
            return next(iter(exc.recipients.values()))[0]
        return 0

    assert await _con_servidor("temporal", codigo) // 100 == 4
    assert await _con_servidor("permanente", codigo) // 100 == 5


async def test_smtp_timeout_agota_al_cliente():
    with pytest.raises((TimeoutError, socket.timeout, smtplib.SMTPServerDisconnected)):
        await _con_servidor("timeout", lambda p: _enviar(p, timeout=0.5))


@pytest.mark.parametrize(
    "opcion",
    [{"mqtt_prueba_fallo_antes_commit": "excepcion"}, {"mqtt_prueba_fallo_antes_commit": "salir"},
     {"smtp_seguridad": "ninguna"}],
)
def test_las_opciones_de_prueba_no_arrancan_en_produccion(opcion, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 48)
    with pytest.raises(ValueError, match="ENVIRONMENT=production"):
        Settings(environment="production", _env_file=None, **opcion)


def test_el_interruptor_de_commit(monkeypatch):
    class _Ajustes:
        mqtt_prueba_fallo_antes_commit = ""

    main._inyectar_fallo_antes_del_commit(_Ajustes())  # apagado: no hace nada
    _Ajustes.mqtt_prueba_fallo_antes_commit = "excepcion"
    with pytest.raises(RuntimeError, match="antes del commit"):
        main._inyectar_fallo_antes_del_commit(_Ajustes())
    salidas = []
    monkeypatch.setattr(main.os, "_exit", lambda codigo: salidas.append(codigo))
    _Ajustes.mqtt_prueba_fallo_antes_commit = "salir"
    main._inyectar_fallo_antes_del_commit(_Ajustes())
    assert salidas == [70]
