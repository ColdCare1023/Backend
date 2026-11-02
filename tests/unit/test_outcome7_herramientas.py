"""Outcome 7: herramientas de prueba (cadenas, huecos de seq_no, latencia, SMTP de falla)."""

import asyncio
import smtplib
import socket
from email.message import EmailMessage

import pytest

from scripts.cadenas_prueba import forjar_insercion, generar_eslabones, recomputar_desde, verificar_eslabones
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


@pytest.mark.parametrize("version,clave", [(1, None), (2, None), (3, b"clave-de-prueba")])
def test_una_insercion_forjada_se_detecta_en_el_eslabon_siguiente(version, clave):
    filas = generar_eslabones("PRUEBA", 100, version, clave)
    forjada = forjar_insercion(filas, 40, clave)
    assert len(forjada) == 101 and forjada[39]["payload"].get("forjado")
    assert verificar_eslabones(forjada, clave) == 41  # el forjado enlaza bien; el que sigue no


@pytest.mark.parametrize("version", [1, 2])
def test_recomputar_sin_clave_deja_la_cadena_coherente_aunque_alterada(version):
    filas = generar_eslabones("PRUEBA", 100, version, None)
    filas[49]["payload"] = {"alterado": True}
    assert verificar_eslabones(filas, None) == 50
    assert verificar_eslabones(recomputar_desde(filas, 50, None), None) is None  # solo un ancla externa lo delata


def test_recomputar_v3_necesita_la_clave_y_con_la_equivocada_se_detecta():
    clave = b"clave-de-prueba"
    filas = generar_eslabones("PRUEBA", 100, 3, clave)
    filas[49]["payload"] = {"alterado": True}
    with pytest.raises(ValueError):
        recomputar_desde(filas, 50, None)
    reescrita = recomputar_desde(filas, 50, b"otra-clave")
    assert verificar_eslabones(reescrita, clave) == 50


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
    r = resumir([*datos, None])
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
     {"mqtt_prueba_fallo_antes_commit": "excepcion_una_vez"},
     {"smtp_seguridad": "ninguna"}],
)
def test_las_opciones_de_prueba_no_arrancan_en_produccion(opcion, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 48)
    with pytest.raises(ValueError, match="ENVIRONMENT=production"):
        Settings(environment="production", _env_file=None, **opcion)


def test_el_modo_una_vez_falla_solo_la_primera(tmp_path, monkeypatch):
    class _Ajustes:
        mqtt_prueba_fallo_antes_commit = "excepcion_una_vez"
        mqtt_prueba_fallo_marca = str(tmp_path / "marca")

    with pytest.raises(RuntimeError, match="antes del commit"):
        main._inyectar_fallo_antes_del_commit(_Ajustes())
    main._inyectar_fallo_antes_del_commit(_Ajustes())  # la marca existe: ya no falla
    (tmp_path / "marca").unlink()  # volver a armar para la repetición siguiente
    with pytest.raises(RuntimeError):
        main._inyectar_fallo_antes_del_commit(_Ajustes())

    salidas = []
    monkeypatch.setattr(main.os, "_exit", lambda codigo: salidas.append(codigo))
    _Ajustes.mqtt_prueba_fallo_antes_commit = "salir_una_vez"
    (tmp_path / "marca").unlink()
    main._inyectar_fallo_antes_del_commit(_Ajustes())
    main._inyectar_fallo_antes_del_commit(_Ajustes())
    assert salidas == [70]


def test_el_interruptor_de_commit(monkeypatch):
    class _Ajustes:
        mqtt_prueba_fallo_antes_commit = ""
        mqtt_prueba_fallo_marca = "/nonexistent/marca"

    main._inyectar_fallo_antes_del_commit(_Ajustes())  # apagado: no hace nada
    _Ajustes.mqtt_prueba_fallo_antes_commit = "excepcion"
    with pytest.raises(RuntimeError, match="antes del commit"):
        main._inyectar_fallo_antes_del_commit(_Ajustes())
    salidas = []
    monkeypatch.setattr(main.os, "_exit", lambda codigo: salidas.append(codigo))
    _Ajustes.mqtt_prueba_fallo_antes_commit = "salir"
    main._inyectar_fallo_antes_del_commit(_Ajustes())
    assert salidas == [70]


def test_el_informe_filtra_por_dispositivo_y_solo_cuenta_correo():
    from datetime import datetime, timezone

    from scripts.informe_latencia import seleccionar

    t = datetime(2026, 11, 1, tzinfo=timezone.utc)
    filas = [
        (t, {"canal": "email", "device_id": "RUN1", "latencia_s": 1.5}),
        (t, {"canal": "sms", "device_id": "RUN1", "latencia_s": None}),
        (t, {"canal": "email", "device_id": "RUN2", "latencia_s": 2.0}),
        (t, '{"canal": "email", "device_id": "RUN1", "latencia_s": 0.9}'),
    ]
    assert [f["latencia_s"] for f in seleccionar(filas, "RUN1")] == [1.5, 0.9]
    assert len(seleccionar(filas, None)) == 3
