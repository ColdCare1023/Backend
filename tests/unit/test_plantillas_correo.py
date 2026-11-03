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
    assert "#039855" in en_rango  # marcador verde dentro de 2-8
    fuera = escala_termica(11.4)
    assert "#d92d20" in fuera  # marcador rojo fuera de rango


@pytest.mark.parametrize("temperatura", [-30.0, 0.0, 2.0, 8.0, 14.0, 60.0])
def test_las_proporciones_de_la_escala_suman_100(temperatura):
    html = escala_termica(temperatura)
    anchos = [int(m) for m in re.findall(r'<td width="(\d+)%" style="padding:0;font-size:0', html)]
    assert len(anchos) == 3 and sum(anchos) == 100 and all(a >= 0 for a in anchos)
    marcador = int(re.search(r'width:(\d+)%;">&nbsp;</td>\s*<td align="center"', html).group(1))
    assert 0 <= marcador <= 97  # el marcador no se sale de la barra
