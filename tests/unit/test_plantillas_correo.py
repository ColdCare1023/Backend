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
    assert 'lang="es"' in html and 'role="presentation"' in html
    assert "React Email" in html and "MIT" in html and "Plus Five Five" in html  # atribución de la estructura base
    assert len(html.encode()) < 60_000  # Gmail recorta los correos de más de ~102 KB
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


@pytest.mark.parametrize("tono", ["critico", "aviso"])
def test_las_alertas_pintan_su_estado_en_una_pildora(tono):
    from src.infrastructure.notifications.plantillas_correo import TONOS

    html = _correo(tono=tono, etiqueta="Excursión <crítica>")
    assert TONOS[tono][0] in html and TONOS[tono][1] in html
    assert "Excursión &lt;crítica&gt;" in html  # el estado va escrito, no solo en color


@pytest.mark.parametrize("tono", ["ok", "info", "marca"])
def test_los_correos_que_no_son_alerta_no_usan_color_de_estado(tono):
    from src.infrastructure.notifications.plantillas_correo import TONOS

    html = _correo(tono=tono, etiqueta="Cuenta creada")
    assert "Cuenta creada" in html
    assert TONOS["critico"][0] not in html and TONOS["aviso"][0] not in html


def test_un_tono_desconocido_falla():
    with pytest.raises(KeyError):
        _correo(tono="rosado")


def test_el_logo_es_imagen_https_con_alt_o_marca_sin_imagen():
    from src.infrastructure.notifications.plantillas_correo import url_logo

    assert url_logo("https://coldcare.vercel.app/") == "https://coldcare.vercel.app/email/coldcare-icon-96.png"
    # Settings.base_frontend quita la barra final: FRONTEND_URL de producción tal cual.
    assert url_logo("https://coldcare.vercel.app") == "https://coldcare.vercel.app/email/coldcare-icon-96.png"
    assert url_logo("https://coldcare.vercel.app/recuperar-contrasena?email=a") == (
        "https://coldcare.vercel.app/email/coldcare-icon-96.png"
    )
    assert url_logo("http://localhost:5173") is None and url_logo("") is None and url_logo(None) is None
    con = _correo(logo_url="https://coldcare.vercel.app/email/coldcare-icon-96.png")
    assert '<img src="https://coldcare.vercel.app/email/coldcare-icon-96.png"' in con
    # Icono decorativo: el nombre va en texto al lado, así se lee también con imágenes bloqueadas.
    assert 'alt=""' in con and ">ColdCare</td>" in con
    sin = _correo()
    assert "<img" not in sin and "ColdCare" in sin  # marca dibujada con celdas
    for malo in ("javascript:alert(1)", "data:image/png;base64,AAAA", "//evil.example/x.png", "http://x.pe/a.png"):
        assert "<img" not in _correo(logo_url=malo) and malo not in _correo(logo_url=malo)
    assert '<img src="../assets/coldcare-icon-96.png"' in _correo(logo_url="../assets/coldcare-icon-96.png")


def test_los_detalles_se_apilan_etiqueta_y_valor_sin_media_queries():
    """Sin `<style>` (Gmail con cuentas que no son de Google) la lista no puede quedar en dos
    columnas estrechas: cada etiqueta va en su fila y el valor debajo, siempre a la izquierda."""
    html = _correo(detalles=[("Dispositivo", "FARM-01"), ("Ubicación", "Botica <Central>")])
    assert "Botica &lt;Central&gt;" in html and "border-bottom:1px solid" in html
    assert "cc-det" not in html and 'width="38%"' not in html and 'text-align:right;">FARM-01' not in html
    assert re.search(r'<td class="cc-fg3" style="[^"]*">Dispositivo</td></tr><tr><td class="cc-fg cc-linea" style="[^"]*text-align:left[^"]*">FARM-01</td>', html)


def test_el_pie_y_el_detalle_aparecen_solo_si_se_piden():
    sin = _correo()
    con = _correo(detalles=[("Dispositivo", "FARM-01")], nota="Verifica el equipo", pie="Pie propio")
    assert "FARM-01" not in sin and "FARM-01" in con and "Verifica el equipo" in con and "Pie propio" in con
    assert "Responde a este correo o habla con el administrador de tu farmacia" in sin  # pie que invita a responder
    assert "no respondas" not in sin.lower()


def test_la_escala_termica_ubica_la_zona_segura_y_marca_la_lectura():
    from src.infrastructure.notifications.plantillas_correo import TONOS

    en_rango = escala_termica(5.0)
    assert "#14B8A6" in en_rango and "rango seguro" in en_rango and "2 °C" in en_rango and "8 °C" in en_rango
    assert TONOS["ok"][0] in en_rango and "Dentro de rango" in en_rango  # marcador verde dentro de 2-8
    fuera = escala_termica(11.4)
    assert TONOS["critico"][0] in fuera and "Fuera de rango" in fuera  # marcador rojo fuera de rango
    cerca = escala_termica(7.6)
    assert TONOS["aviso"][0] in cerca and "Cerca del límite" in cerca  # a menos de 1 °C del límite


@pytest.mark.parametrize("temperatura", [-127.0, -30.0, -12.0, 0.0, 2.0, 8.0, 14.0, 25.0, 60.0, 85.0])
def test_las_proporciones_de_la_escala_suman_100(temperatura):
    html = escala_termica(temperatura)
    anchos = [int(m) for m in re.findall(r'<td width="(\d+)%" style="padding:0;font-size:0;line-height:0;height:6px', html)]
    assert len(anchos) == 3 and sum(anchos) == 100 and all(a >= 0 for a in anchos)
    marcador = int(re.search(r'width:(\d+)%;">&nbsp;</td>\s*<td align="center" width="8%"', html).group(1))
    assert 0 <= marcador <= 92  # el marcador no se sale de la barra


@pytest.mark.parametrize("temperatura", [-127.0, -12.0, 1.2, 5.0, 11.4, 25.0, 85.0])
def test_la_zona_segura_no_se_deforma_con_lecturas_extremas(temperatura):
    """Eje fijo: la zona verde mide lo mismo con -127 °C (sensor desconectado) que con 5 °C, y las
    etiquetas van en su propia tabla de anchos fijos y sin saltos de línea."""
    html = escala_termica(temperatura)
    anchos = [int(m) for m in re.findall(r'<td width="(\d+)%" style="padding:0;font-size:0;line-height:0;height:6px', html)]
    assert anchos == [33, 34, 33]
    assert html.count("table-layout:fixed") == 3
    assert "rango seguro</td>" not in html and "rango seguro 2 a 8 °C" in html
    assert html.count("white-space:nowrap") >= 3


@pytest.mark.parametrize("temperatura", [0.4, 1.2, 9.5, 11.4])
def test_las_etiquetas_del_rango_van_dentro_del_tramo_seguro(temperatura):
    """«2 °C» y «8 °C» van dentro de la celda del tramo seguro, pegadas a sus bordes por dentro: una
    excursión de 1.2 °C o 9.5 °C deja el marcador sobre el riel gris, no encima de la etiqueta."""
    html = escala_termica(temperatura)
    zona = re.search(r'<td width="34%" class="cc-rango"[^>]*>(.*?)</table></td>', html, re.S)
    assert zona, "las dos etiquetas van en la celda del tramo seguro"
    assert re.search(r'text-align:left;">2 °C</td>', zona.group(1)) and "padding-left:4px" in zona.group(1)
    assert re.search(r'text-align:right;">8 °C</td>', zona.group(1)) and "padding-right:4px" in zona.group(1)
    assert zona.group(1).count("font-size:11px") == 2  # caben en el tramo de 64 px (320 px sin `<style>`)
    fila = html.split('class="cc-rango"')[0].rsplit("<tr>", 1)[1]
    assert "°C" not in fila  # nada de texto en el tramo fuera de rango
    marcador = re.search(r'<td align="center" width="8%"[^>]*>&#9660;</td>', html).group(0)
    assert "font-size:14px" in marcador and "line-height:14px" in marcador
    assert "#3F3F46" in html and "#2C2C31" not in html  # riel visible sobre el bloque


def test_las_temperaturas_negativas_usan_el_signo_menos():
    from src.infrastructure.notifications.plantillas_correo import formato_temperatura

    assert "\u2212127.0" in escala_termica(-127.0) and "-127" not in escala_termica(-127.0)
    assert formato_temperatura(-0.04) == "0.0 °C" and formato_temperatura(-3.5) == "\u22123.5 °C"


def test_outlook_recibe_una_tabla_fantasma_de_600_y_la_cabecera_en_una_celda():
    html = _correo()
    assert '<!--[if mso]><table role="presentation" width="600"' in html and "<![endif]-->" in html
    assert '<td class="cc-cabecera"' in html and "<div class=\"cc-cabecera\"" not in html


def test_el_boton_resiste_la_inversion_de_gmail():
    html = _correo(boton=Boton("Ver la alerta", "https://coldcare.example/alertas"))
    assert '<span class="gmail-screen"><span class="gmail-diff">Ver la alerta</span></span>' in html
    assert "u + .body .gmail-screen" in html and 'class="cc-fondo body"' in html


def test_un_valor_largo_de_detalle_va_debajo_de_su_etiqueta_y_a_la_izquierda():
    largo = "Una frase larga que no cabe a la derecha de la etiqueta"
    html = _correo(detalles=[("Dispositivo", "FARM-01"), ("Nota", largo)])
    fila = re.search(r'<td class="cc-fg cc-linea" style="([^"]*)">' + largo, html)
    assert fila and "text-align:left" in fila.group(1)


def test_correo_de_cuenta_creada_tiene_texto_y_html_sin_la_clave():
    from src.infrastructure.notifications.correos_cuenta import correo_cuenta_creada

    c = correo_cuenta_creada("Ana <b>", "ana@prueba.pe", "https://coldcare.example/")
    assert c.asunto == "Tu cuenta de ColdCare está lista"
    assert "ana@prueba.pe" in c.cuerpo and "https://coldcare.example" in c.cuerpo
    assert "Ana &lt;b&gt;" in c.html and "<b>" not in c.html  # nombre escapado
    assert 'href="https://coldcare.example"' in c.html  # sin barra final
    assert "temporal, te la entrega el administrador" in c.html.lower()  # no incluye la clave, solo avisa quién la da
    sin_enlace = correo_cuenta_creada("Ana", "ana@prueba.pe", "")
    assert "Ingresa aquí" not in sin_enlace.cuerpo and "<a href" not in sin_enlace.html


def test_correo_de_recuperar_password_lleva_el_enlace_exacto_y_la_vigencia():
    from src.infrastructure.notifications.correos_cuenta import correo_recuperar_password

    enlace = "https://coldcare.example/restablecer#token=abc_DEF-123"
    c = correo_recuperar_password("Ana", enlace, 30)
    assert enlace in c.cuerpo and f'href="{enlace}"' in c.html
    assert "30 minutos" in c.cuerpo and "30 minutos" in c.html
    assert c.asunto == "Restablece tu contraseña de ColdCare"


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
    assert m.asunto == "Excursión crítica en Refri <1>: 11.4 °C" and "ALERTA CRÍTICA" in m.cuerpo  # texto intacto
    assert "11.4 °C en Refri &lt;1&gt;" in m.html and "Botica Central" in m.html
    from src.infrastructure.notifications.plantillas_correo import TONOS

    assert "rango seguro" in m.html and TONOS["critico"][0] in m.html
    assert f'href="https://coldcare.example/alertas?id={aviso.alerta_id}"' in m.html
    assert '<img src="https://coldcare.example/email/coldcare-icon-96.png"' in m.html  # icono servido por el frontend
    assert "diferida" not in m.html.lower()


def test_el_correo_de_excursion_diferida_lo_dice_y_sin_enlace_no_hay_boton():
    from src.infrastructure.notifications.notificacion_service import componer_correo_excursion

    m = componer_correo_excursion(_aviso(diferida=True), "c@x.pe", "d@y.pe", "", None)
    assert "diferida" in m.html.lower() and "Recibida por el sistema" in m.html
    assert "<a href" not in m.html and m.asunto == "Excursión crítica (lectura diferida) en FARM-01: 11.4 °C"


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
    assert m.asunto == "Riesgo preventivo en Refri 1"
    assert "Margen hasta el límite: 0.4 °C" in m.cuerpo and "0.4 °C" in m.html
    assert "Todavía no es una excursión" in m.cuerpo and "Todavía no es una excursión" in m.html
    from src.infrastructure.notifications.plantillas_correo import TONOS

    assert TONOS["aviso"][0] in m.html  # acento ámbar, no el rojo de la excursión
    assert TONOS["critico"][0] not in m.html
    assert "excursión" not in m.asunto.lower()
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


# ── Códigos de verificación ───────────────────────────────────────────────


def test_el_bloque_del_codigo_es_grande_tabular_y_seleccionable_de_un_toque():
    html = _correo(tono="marca", codigo="048213", codigo_vigencia="Vence en 10 minutos")
    codigo = re.search(r'<p class="cc-codigo"[^>]*>048213</p>', html)
    assert codigo, "el código va como texto plano en un solo nodo"
    estilo = codigo.group(0)
    assert "tabular-nums" in estilo and "font-size:38px" in estilo and "white-space:nowrap" in estilo
    assert "-webkit-user-select:all" in estilo and "user-select:all" in estilo  # se selecciona entero con un toque
    assert 'aria-label="0 4 8 2 1 3"' in html and "Vence en 10 minutos" in html
    assert not re.search(r"<img|<link|@import|url\(", html)
    assert "048213" not in _correo(tono="marca")
    assert "Copiar código" not in html and "copiar-codigo" not in html  # sin URL no hay botón


def test_el_boton_copiar_codigo_lleva_el_codigo_en_el_fragmento():
    from src.infrastructure.notifications.plantillas_correo import AZUL_MARCA

    url = "https://coldcare.example/copiar-codigo#048213"
    html = _correo(tono="marca", codigo="048213", codigo_copiar_url=url)
    boton = re.search(r'<a href="([^"]*)"[^>]*aria-label="Copiar el código 0 4 8 2 1 3"[^>]*>(.*?)</a>', html)
    assert boton and boton.group(1) == url
    assert '<span class="gmail-screen"><span class="gmail-diff">Copiar código</span></span>' in boton.group(2)
    assert f'bgcolor="{AZUL_MARCA}"' in html  # fondo sólido en la celda (Outlook)
    assert html.count("copiar-codigo#") == 1
    # Sin código no se dibuja el botón aunque llegue la URL; una URL que no es http nunca llega al href.
    assert "copiar-codigo" not in _correo(codigo_copiar_url=url)
    malo = _correo(tono="marca", codigo="048213", codigo_copiar_url="javascript:alert(1)")
    assert "javascript:" not in malo and "Copiar código" not in malo


def test_url_copiar_codigo_usa_el_origen_del_frontend_y_el_fragmento():
    from src.infrastructure.notifications.plantillas_correo import url_copiar_codigo

    assert url_copiar_codigo("https://coldcare.vercel.app", "123456") == "https://coldcare.vercel.app/copiar-codigo#123456"
    assert url_copiar_codigo("https://coldcare.vercel.app/", "123456") == "https://coldcare.vercel.app/copiar-codigo#123456"
    assert url_copiar_codigo("https://coldcare.vercel.app/recuperar-contrasena?email=a%40x.pe&modo=activar", "007123") == (
        "https://coldcare.vercel.app/copiar-codigo#007123"
    )
    assert url_copiar_codigo("https://u:p@coldcare.vercel.app", "123456") == "https://coldcare.vercel.app/copiar-codigo#123456"
    # http solo en desarrollo local.
    assert url_copiar_codigo("http://localhost:5173", "123456") == "http://localhost:5173/copiar-codigo#123456"
    assert url_copiar_codigo("http://127.0.0.1:8000/x", "123456") == "http://127.0.0.1:8000/copiar-codigo#123456"
    assert url_copiar_codigo("http://[::1]:5173", "123456") == "http://[::1]:5173/copiar-codigo#123456"
    for malo in ("", None, "javascript:alert(1)", "//evil.example", "ftp://x.pe", "coldcare.vercel.app",
                 "http://coldcare.vercel.app", "http://localhost.evil.example", "http://[::1"):
        assert url_copiar_codigo(malo, "123456") is None, malo
    # Solo 4 a 12 letras o cifras ASCII.
    for malo in ("", None, "123", "1234567890123", "a b&c", "12-456", "١٢٣٤٥٦", "123456\n"):
        assert url_copiar_codigo("https://x.pe", malo) is None, malo
    assert url_copiar_codigo("https://x.pe", "AB12cd") == "https://x.pe/copiar-codigo#AB12cd"


def test_con_codigo_en_fragmento_solo_agrega_un_codigo_valido_a_un_enlace_http():
    from src.infrastructure.notifications.plantillas_correo import con_codigo_en_fragmento

    pagina = "https://coldcare.example/recuperar-contrasena?email=a%40x.pe&modo=activar"
    assert con_codigo_en_fragmento(pagina, "123456") == pagina + "#codigo=123456"
    assert con_codigo_en_fragmento(pagina + "#x", "123456") == pagina + "#x"  # ya trae fragmento
    assert con_codigo_en_fragmento(pagina, "12 34") == pagina
    assert con_codigo_en_fragmento("javascript:alert(1)", "123456") == "javascript:alert(1)"


def _correos_con_codigo():
    from src.infrastructure.notifications import correos_cuenta as c

    pagina = "https://coldcare.example/recuperar-contrasena"
    return {
        "login": c.correo_codigo_login("Ana <b>", "123456", 10),
        "recuperacion": c.correo_codigo_recuperacion("Ana <b>", "123456", 10, f"{pagina}?email=a%40x.pe"),
        "activacion": c.correo_activacion_cuenta("Ana <b>", "a@x.pe", "123456", 48, f"{pagina}?modo=activar"),
    }


@pytest.mark.parametrize("tipo", ["login", "recuperacion", "activacion"])
def test_los_correos_con_codigo_lo_muestran_sin_ponerlo_en_asunto_ni_preheader(tipo):
    correo = _correos_con_codigo()[tipo]
    assert "123456" in correo.cuerpo and "123456" in correo.html
    assert "123456" not in correo.asunto
    preheader = re.search(r'<span style="display:none[^>]*>([^<]*)</span>', correo.html).group(1)
    assert "123456" not in preheader
    assert "Ana &lt;b&gt;" in correo.html and "<b>" not in correo.html
    assert "#0B5CFF" in correo.html
    assert re.search(r"no (fuiste|lo solicitaste|esperabas)", correo.cuerpo + correo.html, re.IGNORECASE)


def test_la_recuperacion_lleva_a_escribir_el_codigo_y_el_enlace_heredado_queda_en_el_texto():
    from src.infrastructure.notifications.correos_cuenta import correo_codigo_recuperacion

    pagina = "https://coldcare.example/recuperar-contrasena?email=a%40x.pe"
    legado = "https://coldcare.example/restablecer-password#token=abc"
    c = correo_codigo_recuperacion("Ana", "123456", 10, pagina, enlace_legado=legado, minutos_enlace=30)
    assert f'href="{pagina}"' in c.html and "Escribir el código" in c.html
    assert legado not in c.html and "#token=" not in c.html and "Pantalla de recuperación" not in c.html
    assert legado in c.cuerpo and "30 minutos" in c.cuerpo


def test_las_alertas_muestran_la_hora_de_peru_corta_y_el_texto_conserva_utc():
    from src.infrastructure.notifications.notificacion_service import componer_correo_excursion

    m = componer_correo_excursion(_aviso(diferida=True), "c@x.pe", "d@y.pe", "", None)
    assert "03/11/2026, 10:00 (hora de Perú)" in m.html and "UTC" not in m.html
    assert "03/11/2026 10:00:00 (hora de Perú)" in m.cuerpo and "2026-11-03 15:00:00 UTC" in m.cuerpo


def test_vigencias_en_minutos_y_horas():
    from src.infrastructure.notifications.correos_cuenta import correo_activacion_cuenta, correo_codigo_login

    assert "10 minutos" in correo_codigo_login("Ana", "123456", 10).cuerpo
    assert "48 horas" in correo_activacion_cuenta("Ana", "a@x.pe", "123456", 48, "https://x.pe/r").cuerpo


def _correos_con_codigo_y_frontend():
    from src.infrastructure.notifications import correos_cuenta as c

    base = "https://coldcare.example"
    pagina = f"{base}/recuperar-contrasena"
    return {
        "login": c.correo_codigo_login("Ana", "123456", 10, base_frontend=base),
        "recuperacion": c.correo_codigo_recuperacion("Ana", "123456", 10, f"{pagina}?email=a%40x.pe&modo=recuperar"),
        "activacion": c.correo_activacion_cuenta(
            "Ana", "a@x.pe", "123456", 48, f"{pagina}?email=a%40x.pe&modo=activar", f"{base}/"
        ),
    }


@pytest.mark.parametrize("tipo", ["login", "recuperacion", "activacion"])
def test_los_correos_con_codigo_traen_el_boton_copiar_codigo(tipo):
    correo = _correos_con_codigo_y_frontend()[tipo]
    assert 'href="https://coldcare.example/copiar-codigo#123456"' in correo.html
    assert correo.html.count("Copiar código") == 1 and "user-select:all" in correo.html
    assert "123456" not in correo.asunto
    preheader = re.search(r'<span style="display:none[^>]*>([^<]*)</span>', correo.html).group(1)
    assert "123456" not in preheader
    assert "copiar-codigo" not in correo.cuerpo  # el texto plano ya trae el código a la vista
    assert correo.html.count("display:none") == 1  # sin texto oculto extra
    if tipo != "login":
        # El botón de la página pasa a secundario: la acción principal es el código.
        assert re.search(r'class="cc-boton2"', correo.html)


def test_el_boton_copiar_codigo_es_la_accion_principal_con_area_tactil_de_44_px():
    """Píldora azul sólida (la mezcla de Gmail incluida) de 12 + 20 + 12 px de alto, y el botón de la
    pantalla, con borde y secundario. El rótulo del código cabe en una línea a 320 px sin `<style>`."""
    for tipo, correo in _correos_con_codigo_y_frontend().items():
        boton = re.search(r'<a href="[^"]*copiar-codigo#123456"[^>]*>', correo.html).group(0)
        assert "padding:12px 24px" in boton and "line-height:20px" in boton, tipo
        assert "mso-padding-alt:12px 24px" in correo.html, tipo
        rotulo = re.search(r'<p class="cc-fg3 cc-rotulo" style="([^"]*)">([^<]*)</p>', correo.html)
        assert "letter-spacing:.6px" in rotulo.group(1), tipo
        assert rotulo.group(2) == ("Tu código de activación" if tipo == "activacion" else "Tu código de verificación")
    assert ".cc-rotulo { letter-spacing:1.2px !important; }" in correo.html  # más aire en escritorio


@pytest.mark.parametrize("tipo", ["recuperacion", "activacion"])
def test_el_boton_de_la_pantalla_rellena_el_codigo_y_la_direccion_en_texto_va_sin_el(tipo):
    """«Escribir el código» / «Activar mi cuenta» llevan `#codigo=…` (fragmento: no llega al servidor)
    para que la pantalla lo rellene; la dirección de respaldo y el texto plano van sin el fragmento."""
    correo = _correos_con_codigo_y_frontend()[tipo]
    pagina = re.search(r'href="(https://coldcare\.example/recuperar-contrasena\?[^"#]*)#codigo=123456"', correo.html)
    assert pagina, "el botón de la pantalla lleva el código en el fragmento"
    secundario = re.search(r'<td [^>]*class="cc-boton2"[^>]*><a href="([^"]*)"', correo.html)
    assert secundario.group(1).endswith("#codigo=123456")
    respaldo = re.search(r'<a href="([^"]*)" target="_blank" class="cc-enlace"[^>]*>([^<]*)</a>', correo.html)
    assert respaldo.group(1) == respaldo.group(2) == pagina.group(1)  # texto y href coinciden, sin el código
    assert "#codigo=" not in correo.cuerpo and correo.html.count("#codigo=") == 1
    assert "123456" not in correo.asunto


def test_la_firma_de_la_tarjeta_es_un_filo_de_2_px_que_nace_y_muere_en_transparente():
    """Sin degradado el filo desaparece (fondo grafito, no azul) y al invertirlo no deja extremos oscuros;
    los tres resplandores (cian, azul y rosa) también terminan en transparente sobre el grafito."""
    from src.infrastructure.notifications.plantillas_correo import AZUL_MARCA

    html = _correo(tono="marca")
    filo = re.search(r'<td height="2" bgcolor="([^"]+)"[^>]*style="([^"]*)"', html)
    assert filo and filo.group(1) == "#141416"
    assert "height:2px" in filo.group(2) and "background-color:#141416" in filo.group(2)
    assert "linear-gradient(90deg,rgba(34,211,238,0),#22D3EE 20%,#0B5CFF 50%,#F472B6 80%,rgba(244,114,182,0))" in filo.group(2)
    assert f'bgcolor="{AZUL_MARCA}" style="height' not in html
    panel = re.search(r'<td class="cc-panel cc-tarjeta" bgcolor="#141416" style="([^"]*)"', html).group(1)
    for brillo in ("rgba(11,92,255,.18)", "rgba(34,211,238,.08) 0,rgba(34,211,238,0)", "rgba(244,114,182,.08) 0,rgba(244,114,182,0)"):
        assert brillo in panel, brillo
    assert "at 15% 0" in panel and "at 85% 0" in panel and "background-color:#141416" in panel


def test_los_correos_sin_codigo_no_traen_el_boton_copiar():
    for _, _, html in _todos_los_correos():
        if html and "cc-codigo" not in html:
            assert "copiar-codigo" not in html and "Copiar código" not in html


def test_el_login_sin_frontend_no_dibuja_el_boton_y_con_frontend_trae_el_icono():
    from src.infrastructure.notifications.correos_cuenta import correo_codigo_login

    assert "copiar-codigo" not in correo_codigo_login("Ana", "123456", 10).html
    con = correo_codigo_login("Ana", "123456", 10, base_frontend="https://coldcare.example")
    assert '<img src="https://coldcare.example/email/coldcare-icon-96.png"' in con.html


def test_el_caso_de_uso_de_login_pasa_el_frontend_al_correo():
    from datetime import timedelta
    from types import SimpleNamespace
    from uuid import uuid4

    from src.application.use_cases.codigos_verificacion import CodigoEmitido
    from src.application.use_cases.login_con_codigo import _desafio

    usuario = SimpleNamespace(id=uuid4(), nombre="Ana", email="ana@x.pe")
    emitido = CodigoEmitido(codigo="654321", registro=None, desafio="d", vigencia=timedelta(minutes=10))
    servicio = SimpleNamespace(politica=SimpleNamespace(reenvio=timedelta(seconds=60)))
    desafio = _desafio(usuario, "d", emitido, servicio, None, "https://coldcare.example")
    assert 'href="https://coldcare.example/copiar-codigo#654321"' in desafio.html
    assert "654321" not in desafio.asunto
    sin = _desafio(usuario, "d", emitido, servicio)
    assert "copiar-codigo" not in sin.html and "654321" in sin.html


def test_el_enlace_de_respaldo_es_un_enlace_neutro_y_legible_en_modo_oscuro():
    html = _correo(boton=Boton("Ver la alerta", "https://coldcare.example/alertas?x=1&y=2"))
    assert '<a href="https://coldcare.example/alertas?x=1&amp;y=2" target="_blank" class="cc-enlace"' in html
    assert ".cc-enlace { color:#A3A3A9 !important; }" in html
    # Enlaces que detectan Apple Mail y Gmail (fechas, correos): heredan el color del texto.
    assert "a[x-apple-data-detectors]" in html and "u + .body a, #MessageViewBody a" in html
    oscuro = html.split("prefers-color-scheme: dark")[1]
    assert not re.search(r"(^|[\s}])a\s*\{", oscuro)  # sin regla `a{}` que pise el texto blanco del botón
    assert _correo(boton=Boton("x", "javascript:alert(1)")).count("cc-enlace") == 1  # solo la regla CSS


# ── Entregabilidad: que no parezcan spam ─────────────────────────────────


def _todos_los_correos():
    """(asunto, texto, html o None) de cada correo que sale del sistema, con un equipo sin nombre
    (el asunto lleva el id del nodo) para revisar solo el texto propio del asunto."""
    from dataclasses import replace
    from uuid import uuid4

    from src.application.use_cases.vigilar_sin_datos import AvisoSinDatos
    from src.domain.value_objects.nivel_riesgo import NivelRiesgo
    from src.infrastructure.notifications import correos_cuenta as c
    from src.infrastructure.notifications import notificacion_service as n

    pagina = "https://coldcare.example/recuperar-contrasena"
    cuenta = [
        c.correo_cuenta_creada("Ana", "a@x.pe", "https://coldcare.example"),
        c.correo_recuperar_password("Ana", "https://coldcare.example/restablecer#token=t", 30),
        *_correos_con_codigo().values(),
    ]
    correos = [(m.asunto, m.cuerpo, m.html) for m in cuenta]
    excursion = _aviso()
    alertas = [
        n.componer_correo_excursion(excursion, "c@x.pe", "d@y.pe", "https://coldcare.example"),
        n.componer_correo_excursion(_aviso(diferida=True), "c@x.pe", "d@y.pe"),
        n.componer_correo_preventivo(
            replace(_aviso(temperatura=7.6), nivel=NivelRiesgo.RIESGO_PREVENTIVO), "c@x.pe", "d@y.pe", pagina
        ),
    ]
    for tipo in ("sin_datos", "monitoreo_suspendido", "excursion_no_verificada", "sensor_fault", "colision_clave"):
        alertas.append(n.componer_correo_sin_datos(
            AvisoSinDatos(incidente_id=uuid4(), device_id="FARM-01", abierto_en=excursion.captured_at,
                          ultimo_reporte_en=excursion.captured_at, umbral_segundos=90, tipo=tipo,
                          detalle={"temperatura_interna": 11.5}),
            "c@x.pe", "d@y.pe",
        ))
    return correos + [(m.asunto, m.cuerpo, m.html) for m in alertas]


def test_los_asuntos_son_frases_sin_corchetes_ni_mayusculas_sostenidas():
    """Gmail mandó la activación a Spam «por parecerse a otros mensajes»: nada de «[ColdCare]» ni de
    palabras en MAYÚSCULAS en el asunto, que es lo que se lee en la notificación del teléfono."""
    asuntos = [asunto for asunto, _, _ in _todos_los_correos()]
    assert len(asuntos) == 13
    for asunto in asuntos:
        propio = asunto.replace("FARM-01", "")
        assert "[" not in propio and "]" not in propio, asunto
        assert not re.search(r"\b[A-ZÁÉÍÓÚÑ]{3,}\b", propio), asunto
        assert asunto[0].isupper() and len(asunto) <= 60, asunto


def test_cada_correo_html_trae_texto_plano_y_un_pie_que_invita_a_responder():
    for asunto, texto, html in _todos_los_correos():
        assert len(texto) > 80, asunto  # versión de texto plano con contenido real
        if html is not None:
            assert "¿Dudas? Responde a este correo" in html and "no respondas" not in html.lower(), asunto
            # Lo único oculto es el preheader estándar.
            assert html.count("display:none") == 1, asunto


@pytest.mark.parametrize("alta", ["cuenta_creada", "activacion"])
def test_los_correos_de_alta_no_traen_filas_de_credenciales(alta):
    """Nada de «Usuario: … / Contraseña: …» (texto ni tabla): el acceso se explica en una frase."""
    from src.infrastructure.notifications import correos_cuenta as c

    correo = (
        c.correo_cuenta_creada("Ana", "a@x.pe", "https://coldcare.example")
        if alta == "cuenta_creada"
        else c.correo_activacion_cuenta("Ana", "a@x.pe", "123456", 48, "https://coldcare.example/r")
    )
    assert not re.search(r"(?mi)^\s*(usuario|contraseña|clave)\s*:", correo.cuerpo)
    assert "Tu usuario es" not in correo.cuerpo
    assert not re.search(r">\s*(Usuario|Contraseña|Clave)\s*</td>", correo.html)
    assert "Entrarás con este mismo correo (a@x.pe)" in correo.cuerpo and "a@x.pe" in correo.html
    if alta == "activacion":
        assert "Tú eliges tu contraseña al activar la cuenta" in correo.cuerpo
        assert "Tú eliges tu contraseña al activar la cuenta" in correo.html
