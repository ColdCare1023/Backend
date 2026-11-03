"""Plantilla HTML de los correos: estructura, escape y escala térmica."""

import re

import pytest

from src.infrastructure.notifications.plantillas_correo import Boton, escala_termica, renderizar


def _correo(**extra):
    base = {
        "asunto": "Asunto", "preheader": "Vista previa", "tono": "critico", "etiqueta": "Alerta",
        "titulo": "Título", "parrafos": ["Hola"],
    }
    return renderizar(**{**base, **extra})


def test_el_documento_es_completo_y_no_carga_nada_externo():
    html = _correo(boton=Boton("Abrir", "https://coldcare.example/alertas"))
    assert html.startswith("<!DOCTYPE html>") and "</html>" in html
    assert "prefers-color-scheme: dark" in html and 'name="viewport"' in html
    assert not re.search(r"<img|<link|@import|url\(", html)  # sin imágenes ni recursos remotos
    assert 'href="https://coldcare.example/alertas"' in html and "Abrir" in html


def test_todo_texto_dinamico_se_escapa():
    html = _correo(titulo="<script>alert(1)</script>", parrafos=["a & b <b>x</b>"],
                   detalles=[("Equipo", '"><img src=x onerror=1>')], nota="<i>x</i>")
    assert "<script>" not in html and "<img" not in html and "<b>x</b>" not in html
    assert "&lt;script&gt;" in html and "a &amp; b" in html


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,x", "//evil.example", "ftp://x"])
def test_un_enlace_que_no_es_http_nunca_llega_al_href(url):
    html = _correo(boton=Boton("Abrir", url))
    assert 'href="#"' in html and url not in html


@pytest.mark.parametrize("tono", ["critico", "aviso", "ok", "info"])
def test_cada_tono_pinta_su_acento(tono):
    from src.infrastructure.notifications.plantillas_correo import TONOS

    assert TONOS[tono][0] in _correo(tono=tono)


def test_el_pie_y_el_detalle_aparecen_solo_si_se_piden():
    sin = _correo()
    con = _correo(detalles=[("Dispositivo", "FARM-01")], nota="Verifica el equipo", pie="Pie propio")
    assert "FARM-01" not in sin and "FARM-01" in con and "Verifica el equipo" in con and "Pie propio" in con
    assert "no respondas" in sin


def test_la_escala_termica_ubica_la_zona_segura_y_marca_la_lectura():
    en_rango = escala_termica(5.0)
    assert "#12b76a" in en_rango and "rango seguro" in en_rango and "2 °C" in en_rango and "8 °C" in en_rango
    assert "#0e9f6e" in en_rango  # marcador verde dentro de 2-8
    fuera = escala_termica(11.4)
    assert "#e5382b" in fuera  # marcador rojo fuera de rango


@pytest.mark.parametrize("temperatura", [-30.0, 0.0, 2.0, 8.0, 14.0, 60.0])
def test_las_proporciones_de_la_escala_suman_100(temperatura):
    html = escala_termica(temperatura)
    anchos = [int(m) for m in re.findall(r'<td width="(\d+)%" style="padding:0;font-size:0', html)]
    assert len(anchos) == 3 and sum(anchos) == 100 and all(a >= 0 for a in anchos)
    marcador = int(re.search(r'width:(\d+)%;">&nbsp;</td>\s*<td align="center"', html).group(1))
    assert 0 <= marcador <= 97  # el marcador no se sale de la barra


def test_correo_de_cuenta_creada_tiene_texto_y_html_sin_la_clave():
    from src.infrastructure.notifications.correos_cuenta import correo_cuenta_creada

    c = correo_cuenta_creada("Ana <b>", "ana@prueba.pe", "https://coldcare.example/")
    assert c.asunto == "[ColdCare] Tu cuenta fue creada"
    assert "ana@prueba.pe" in c.cuerpo and "https://coldcare.example" in c.cuerpo
    assert "Ana &lt;b&gt;" in c.html and "<b>" not in c.html  # nombre escapado
    assert 'href="https://coldcare.example"' in c.html  # sin barra final
    assert "temporal" in c.html.lower() and "contraseña:" not in c.html.lower().replace("contraseña", "contraseña:", 0)
    sin_enlace = correo_cuenta_creada("Ana", "ana@prueba.pe", "")
    assert "Ingresa aquí" not in sin_enlace.cuerpo and "<a href" not in sin_enlace.html


def test_correo_de_recuperar_password_lleva_el_enlace_exacto_y_la_vigencia():
    from src.infrastructure.notifications.correos_cuenta import correo_recuperar_password

    enlace = "https://coldcare.example/restablecer#token=abc_DEF-123"
    c = correo_recuperar_password("Ana", enlace, 30)
    assert enlace in c.cuerpo and f'href="{enlace}"' in c.html
    assert "30 minutos" in c.cuerpo and "30 minutos" in c.html
    assert c.asunto == "[ColdCare] Restablecer contraseña"


def _aviso(temperatura=11.4, diferida=False):
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4

    from src.application.use_cases.generar_alerta import AvisoExcursion

    captura = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)
    return AvisoExcursion(
        alerta_id=uuid4(), device_id="FARM-01", temperatura=temperatura, captured_at=captura,
        received_at=captura + timedelta(hours=2) if diferida else captura,
        time_quality="synced",
    )


def test_el_correo_de_excursion_trae_escala_boton_y_datos():
    from src.infrastructure.notifications.notificacion_service import componer_correo_excursion

    aviso = _aviso()
    m = componer_correo_excursion(aviso, "c@x.pe", "d@y.pe", "https://coldcare.example",
                                  {"nombre": "Refri <1>", "ubicacion": "Botica Central"})
    assert m.asunto == "[ColdCare] EXCURSIÓN CRÍTICA — FARM-01" and "ALERTA CRÍTICA" in m.cuerpo  # texto intacto
    assert "11.4 °C en Refri &lt;1&gt;" in m.html and "Botica Central" in m.html
    assert "rango seguro" in m.html and "#e5382b" in m.html
    assert f'href="https://coldcare.example/alertas?id={aviso.alerta_id}"' in m.html
    assert "diferida" not in m.html.lower()


def test_el_correo_de_excursion_diferida_lo_dice_y_sin_enlace_no_hay_boton():
    from src.infrastructure.notifications.notificacion_service import componer_correo_excursion

    m = componer_correo_excursion(_aviso(diferida=True), "c@x.pe", "d@y.pe", "", None)
    assert "diferida" in m.html.lower() and "Recibida por el sistema" in m.html
    assert "<a href" not in m.html and m.asunto.endswith("— FARM-01") and "[DIFERIDA]" in m.asunto


def test_el_correo_de_excursion_sin_dato_de_sensor_no_dibuja_escala():
    from src.infrastructure.notifications.notificacion_service import componer_correo_excursion

    m = componer_correo_excursion(_aviso(temperatura=None), "c@x.pe", "d@y.pe", "", {})
    assert "rango seguro" not in m.html and "sin dato de sensor" in m.html


def test_el_correo_preventivo_avisa_con_margen_y_sin_exagerar():
    from dataclasses import replace

    from src.domain.value_objects.nivel_riesgo import NivelRiesgo
    from src.infrastructure.notifications.notificacion_service import componer_correo_preventivo

    aviso = replace(_aviso(temperatura=7.6), nivel=NivelRiesgo.RIESGO_PREVENTIVO)
    m = componer_correo_preventivo(aviso, "c@x.pe", "d@y.pe", "https://coldcare.example", {"nombre": "Refri 1"})
    assert m.asunto == "[ColdCare] RIESGO PREVENTIVO — FARM-01"
    assert "Margen hasta el límite: 0.4 °C" in m.cuerpo and "0.4 °C" in m.html
    assert "Todavía no es una excursión" in m.cuerpo and "Todavía no es una excursión" in m.html
    assert "#e07b00" in m.html  # acento ámbar, no el rojo de la excursión
    assert "EXCURSIÓN CRÍTICA" not in m.asunto
    assert "rango seguro" in m.html and "Refri 1 se acerca al límite" in m.html
    assert f"/alertas?id={aviso.alerta_id}" in m.html


async def test_el_aviso_sale_a_varios_destinatarios_y_la_auditoria_los_enmascara():
    from src.infrastructure.config import Settings
    from src.infrastructure.notifications.transporte_email import (
        HttpApiTransporte,
        MensajeCorreo,
        SmtpTransporte,
    )
    from src.infrastructure.security.privacidad import enmascarar_emails

    capturado = {}

    class _Smtp(SmtpTransporte):
        def _enviar_bloqueante(self, correo):
            capturado["to"] = correo["To"]

    ajustes = Settings(environment="test", _env_file=None, jwt_secret_key="x" * 48)
    await _Smtp(ajustes).enviar(MensajeCorreo("c@x.pe", "diego@gmail.com, brenda@gmail.com", "A", "t"))
    assert "diego@gmail.com" in capturado["to"] and "brenda@gmail.com" in capturado["to"]

    enviados = []

    class _Resp:
        def raise_for_status(self):
            return None

    class _Cliente:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return None

        async def post(self, url, headers=None, json=None):
            enviados.append(json)
            return _Resp()

    import src.infrastructure.notifications.transporte_email as modulo

    original = modulo.httpx.AsyncClient
    modulo.httpx.AsyncClient = _Cliente
    try:
        await HttpApiTransporte(ajustes).enviar(MensajeCorreo("c@x.pe", "a@x.com, b@y.com,", "A", "t"))
    finally:
        modulo.httpx.AsyncClient = original
    assert enviados[0]["to"] == ["a@x.com", "b@y.com"]

    enmascarado = enmascarar_emails("diego@gmail.com, brenda@gmail.com")
    assert "diego" not in enmascarado and "brenda" not in enmascarado and enmascarado.count("***") == 2
