"""Plantilla HTML de los correos de ColdCare: diseño «Grafito».

Un solo diseño oscuro para todos los avisos (cuenta creada, contraseña, códigos, alertas), con los
neutros del modo oscuro de la app: página casi negra (#0B0B0C), tarjeta grafito (#141416) con borde
fino (#27272A), titular grande alineado a la izquierda y texto secundario gris (#A3A3A9). El
degradado de marca (cian → azul → rosa, el arco del icono) firma la tarjeta: un filo de luz de 2 px
en el borde superior y, debajo, tres resplandores (cian, azul y rosa) que se apagan sobre el grafito.
La estructura (logo arriba, tarjeta, bloque del código, nota de
seguridad bajo una línea fina y pie fuera de la tarjeta) parte de las plantillas de ejemplo de React
Email (https://github.com/resend/react-email, apps/demo/emails); no se copió ninguna marca, imagen ni
texto de esas plantillas.

Licencia de las plantillas de ejemplo (MIT):
    Copyright 2024 Plus Five Five, Inc
    Permission is hereby granted, free of charge, to any person obtaining a copy of this software
    and associated documentation files (the "Software"), to deal in the Software without
    restriction, including without limitation the rights to use, copy, modify, merge, publish,
    distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the
    Software is furnished to do so, subject to the following conditions: The above copyright notice
    and this permission notice shall be included in all copies or substantial portions of the
    Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.

Está hecha para clientes de correo, no para navegadores:

- tablas `role="presentation"` y estilos en línea (Gmail, Outlook y Apple Mail recortan el CSS
  externo); el bloque `<style>` solo lleva media queries y retoques;
- 600 px de ancho máximo, fluida hasta 375 px (y legible a 320 px aunque se descarte el `<style>`);
- oscura en todos los clientes: cada superficie lleva `bgcolor` sólido, así que un cliente en modo
  claro la muestra igual. Los degradados son solo adorno y siempre tienen un color sólido debajo
  (Gmail descarta algunos `background-image`);
- en la app de Gmail (que invierte colores por su cuenta) el texto de los botones azules vuelve a
  blanco con la mezcla `gmail-screen` / `gmail-diff`;
- sin fuentes remotas; la única imagen es el icono de marca y solo si se pasa `logo_url` (sin él se
  dibuja una marca con celdas; con imágenes bloqueadas queda la baldosa azul marino y el nombre en texto);
- contraste AA sobre grafito; el estado nunca depende solo del color (va escrito);
- todo texto dinámico pasa por `html.escape`.

El correo siempre lleva además la versión de texto plano (ver `MensajeCorreo.cuerpo`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from urllib.parse import quote, urlsplit

# Tono → (color de estado con contraste AA sobre grafito, fondo tintado de la píldora).
TONOS: dict[str, tuple[str, str]] = {
    "critico": ("#F97066", "#2A1412"),  # carmesí
    "aviso": ("#FDB022", "#2A1F0D"),  # ámbar
    "ok": ("#2DD4BF", "#0D2522"),  # teal
    "info": ("#8AB4FF", "#121D33"),
    # Correos de cuenta con código (login, recuperación, activación): azul de marca.
    "marca": ("#8AB4FF", "#121D33"),
}
# Borde de la píldora de las alertas (un paso más claro que su fondo).
_BORDE_PILDORA = {"critico": "#5A231E", "aviso": "#584015"}
# Solo las alertas llevan píldora de color; el resto, un punto de color y la etiqueta en gris.
_TONOS_DE_ESTADO = ("critico", "aviso")

# Marca: azul primario (botones) y azul marino (baldosa del icono).
AZUL_MARCA = "#0B5CFF"
AZUL_MARINO = "#0F172A"
# Degradado del arco del icono. Solo adorno: siempre con un color sólido debajo.
_CIAN = "#22D3EE"
_ROSA = "#F472B6"

# Neutros grafito (modo oscuro de la app).
_PAGINA = "#0B0B0C"
_TARJETA = "#141416"
_BLOQUE = "#1B1B1E"  # código, lectura y detalles: un escalón sobre la tarjeta
_BORDE = "#27272A"
_BORDE_BLOQUE = "#2E2E33"
_BORDE_BLOQUE_ALTO = "#3A3A40"  # filo superior del bloque: luz cenital
_FG = "#FAFAFA"
_FG2 = "#A3A3A9"  # texto secundario: 7,4:1 sobre la tarjeta
_FG3 = "#8B8B93"  # etiquetas y pie: 5,5:1 sobre la tarjeta, 5,8:1 sobre la página
_PISTA = "#3F3F46"  # tramo fuera de rango de la barra térmica (1,7:1 sobre el bloque: se ve el riel)
_VERDE_RANGO = "#14B8A6"  # zona segura 2-8 °C (teal)

# Resplandores bajo el filo de luz: cian a la izquierda, azul al centro y rosa a la derecha, como el
# arco del icono. Todos terminan en transparente: sin degradados (Gmail descarta algunos) queda el
# grafito sólido de la celda, sin bandas ni bordes duros.
_RESPLANDOR = (
    "radial-gradient(42% 150px at 15% 0,rgba(34,211,238,.08) 0,rgba(34,211,238,0) 100%),"
    "radial-gradient(60% 190px at 50% 0,rgba(11,92,255,.18) 0,rgba(11,92,255,0) 100%),"
    "radial-gradient(42% 150px at 85% 0,rgba(244,114,182,.08) 0,rgba(244,114,182,0) 100%)"
)

MARCA = "ColdCare"
LEMA = "Monitoreo de cadena de frío"
RUTA_LOGO = "/email/coldcare-icon-96.png"
# Códigos que pueden viajar en un enlace: solo letras y cifras ASCII (el código es de 6 cifras).
_CODIGO_EN_ENLACE = re.compile(r"[0-9A-Za-z]{4,12}")

_FUENTE = "Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"

_ATRIBUCION = (
    "<!-- Diseño «Grafito» de ColdCare. Estructura a partir de las plantillas de ejemplo de React Email "
    "(github.com/resend/react-email). MIT License. Copyright 2024 Plus Five Five, Inc. -->"
)


@dataclass(frozen=True, slots=True)
class Boton:
    """`respaldo`: dirección que se muestra bajo el botón («¿El botón no funciona?…») si difiere del
    enlace, p. ej. la pantalla del código sin el `#codigo=` que la rellena."""

    texto: str
    url: str
    respaldo: str | None = None


def _e(valor: object) -> str:
    return escape(str(valor), quote=True)


def _url_segura(url: str) -> str:
    """Solo http(s): un enlace `javascript:` o `data:` no debe llegar nunca a un href."""
    return _e(url) if url.lower().startswith(("http://", "https://")) else "#"


def _codigo_para_enlace(codigo: str | None) -> bool:
    return bool(codigo) and _CODIGO_EN_ENLACE.fullmatch(codigo or "") is not None


def url_logo(frontend_url: str | None) -> str | None:
    """URL HTTPS del icono de marca, sin credenciales, consulta ni fragmento."""
    if not frontend_url:
        return None
    try:
        partes = urlsplit(frontend_url.strip())
        host = partes.netloc.rpartition("@")[2]
        if partes.scheme != "https" or not partes.hostname:
            return None
    except ValueError:
        return None
    return f"https://{host}{RUTA_LOGO}"


def con_codigo_en_fragmento(url: str, codigo: str | None) -> str:
    """`url#codigo=<código>`: la pantalla donde se escribe el código lo rellena sola.

    Va en el fragmento, que el navegador no envía al servidor; el código ya está a la vista en el
    cuerpo del correo, así que no lo expone más. Si la URL no es http(s), ya trae un fragmento o el
    código no es de 4 a 12 letras o cifras, la devuelve igual."""
    if not _codigo_para_enlace(codigo) or "#" in url or not url.lower().startswith(("https://", "http://")):
        return url
    return f"{url}#codigo={quote(codigo or '', safe='')}"


def _src_logo(logo_url: str | None) -> str | None:
    """https absoluto o ruta relativa (vista previa local). Nada de `javascript:`, `data:` ni `//host`."""
    if not logo_url:
        return None
    if logo_url.lower().startswith("https://"):
        return _e(logo_url)
    if ":" in logo_url or logo_url.startswith("//"):
        return None
    return _e(logo_url)


def _texto(contenido: str, *, tam: int = 15, alto: int = 24, color: str = _FG2, clase: str = "cc-fg2",
           margen: str = "0", alinear: str = "left", peso: int = 400, extra: str = "") -> str:
    return (
        f'<p class="{clase}" style="margin:{margen};font-family:{_FUENTE};font-size:{tam}px;'
        f'line-height:{alto}px;font-weight:{peso};color:{color};text-align:{alinear};{extra}">{contenido}</p>'
    )


def _tabla(contenido: str, estilo: str = "", clase: str = "", atributos: str = "") -> str:
    cls = f' class="{clase}"' if clase else ""
    return (
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"{cls}{atributos} '
        f'style="border-collapse:separate;{estilo}">{contenido}</table>'
    )


def _bloque(contenido: str, padding: str = "24px 24px", margen: str = "0 0 24px 0") -> str:
    """Celda grafito un escalón más clara que la tarjeta (código, lectura y detalles).

    El borde superior es más claro que los demás: da sensación de relieve sin sombras, que la
    mayoría de clientes descarta."""
    return _tabla(
        f'<tr><td class="cc-bloque" bgcolor="{_BLOQUE}" style="padding:{padding};background-color:{_BLOQUE};'
        f"border:1px solid {_BORDE_BLOQUE};border-top-color:{_BORDE_BLOQUE_ALTO};border-radius:12px;"
        f'">{contenido}</td></tr>',
        estilo=f"margin:{margen};",
    )


# ── Marca ────────────────────────────────────────────────────────────────


def _marca_celdas(tam: int) -> str:
    """Marca sin imagen: baldosa azul marino redondeada con la «C» (si no hay `logo_url`)."""
    radio = round(tam * 0.26)
    letra = round(tam * 0.5)
    return (
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr>'
        f'<td align="center" valign="middle" width="{tam}" height="{tam}" bgcolor="{AZUL_MARINO}" '
        f'style="width:{tam}px;height:{tam}px;background-color:{AZUL_MARINO};border-radius:{radio}px;'
        f"border-bottom:{max(2, tam // 16)}px solid {_CIAN};font-family:{_FUENTE};font-size:{letra}px;"
        f'line-height:{tam}px;font-weight:700;color:#FFFFFF;mso-line-height-rule:exactly;">C</td>'
        "</tr></table>"
    )


def _logo(src: str | None, tam: int) -> str:
    """Icono decorativo (`alt=""`): el nombre de la marca va en texto al lado. Con imágenes bloqueadas
    queda la baldosa azul marino de la celda, del mismo tamaño y radio que el icono."""
    if src is None:
        return _marca_celdas(tam)
    radio = round(tam * 0.22)
    return (
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr>'
        f'<td width="{tam}" height="{tam}" bgcolor="{AZUL_MARINO}" style="width:{tam}px;height:{tam}px;'
        f'background-color:{AZUL_MARINO};border-radius:{radio}px;font-size:0;line-height:0;">'
        f'<img src="{src}" width="{tam}" height="{tam}" alt="" '
        f'style="display:block;width:{tam}px;height:{tam}px;border:0;outline:none;text-decoration:none;'
        f'border-radius:{radio}px;"></td></tr></table>'
    )


def _cabecera(src: str | None) -> str:
    """Icono y nombre de la marca sobre la tarjeta, alineados a la izquierda."""
    return (
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr>'
        f'<td valign="middle" style="padding:0;">{_logo(src, 32)}</td>'
        f'<td valign="middle" class="cc-fg" style="padding:0 0 0 12px;font-family:{_FUENTE};font-size:17px;'
        f'line-height:24px;font-weight:600;letter-spacing:-.2px;color:{_FG};">{MARCA}</td>'
        "</tr></table>"
    )


def _filo_de_luz() -> str:
    """Firma del diseño: línea de 2 px con el degradado del arco del icono en el borde superior de la
    tarjeta, que nace y muere en transparente. Debajo va el grafito de la tarjeta, no un color de
    marca: si el cliente descarta el degradado la línea desaparece (en vez de quedar un tramo azul
    duro), y si lo invierte (Gmail en iPhone) no quedan extremos oscuros sobre la tarjeta clara."""
    return _tabla(
        f'<tr><td height="2" bgcolor="{_TARJETA}" class="cc-tarjeta" style="height:2px;padding:0;font-size:0;'
        f"line-height:2px;mso-line-height-rule:exactly;background-color:{_TARJETA};"
        f"background-image:linear-gradient(90deg,rgba(34,211,238,0),{_CIAN} 20%,{AZUL_MARCA} 50%,"
        f'{_ROSA} 80%,rgba(244,114,182,0));">&nbsp;</td></tr>'
    )


# ── Bloques ──────────────────────────────────────────────────────────────


def _estado_lectura(temperatura: float, minimo: float, maximo: float) -> tuple[str, str]:
    if temperatura < minimo or temperatura > maximo:
        return "critico", "Fuera de rango"
    if min(temperatura - minimo, maximo - temperatura) <= 1.0:
        return "aviso", "Cerca del límite"
    return "ok", "Dentro de rango"


def _num(valor: float, formato: str = ".1f") -> str:
    """Número para mostrar: signo menos tipográfico (U+2212), que los lectores de pantalla leen «menos»."""
    texto = format(valor, formato)
    if texto.lstrip("-").strip("0.") == "":  # «-0.0» → «0.0»
        texto = texto.lstrip("-")
    return texto.replace("-", "−")


def formato_temperatura(temperatura: float) -> str:
    """«-127.0 °C» con U+2212: lectura con un decimal y signo menos tipográfico, para el HTML."""
    return f"{_num(temperatura)} °C"


def escala_termica(temperatura: float, minimo: float = 2.0, maximo: float = 8.0) -> str:
    """Lectura destacada + barra con la zona segura (2-8 °C) en teal y un marcador en la lectura.

    El estado va escrito («Fuera de rango», «Cerca del límite», «Dentro de rango»): el color solo lo
    refuerza. El eje es fijo (6 °C a cada lado del rango, -4 a 14 °C), así la zona segura ocupa siempre
    el mismo tercio de la barra; una lectura fuera del eje (p. ej. -127 °C o 85 °C, valores de un
    sensor desconectado) deja el marcador pegado al extremo y el valor exacto queda en el número grande.

    La barra, el marcador y las etiquetas van en tres tablas con `table-layout:fixed` y los mismos
    anchos: el texto de las etiquetas no puede ensanchar las celdas de la barra. «2 °C» y «8 °C» van
    dentro del tramo seguro, pegadas a sus bordes por dentro: una lectura de 1.2 °C o de 9.5 °C (las
    excursiones más comunes) deja el marcador sobre el riel gris, fuera de la zona y lejos de la
    etiqueta, y no parece «en el límite». Con el bloque a 16 px de los lados, en 320 px sin `<style>`
    el tramo mide 64 px y las etiquetas de 11 px (24 px cada una, más 4 px por dentro) dejan 9 px
    entre ellas; con la media query, el tramo mide 75 px."""
    inferior = minimo - 6.0
    superior = maximo + 6.0
    ancho = superior - inferior

    def pct(valor: float) -> float:
        return (min(max(valor, inferior), superior) - inferior) / ancho * 100.0

    izquierda = round(pct(minimo))
    zona = round(pct(maximo)) - izquierda
    derecha = 100 - izquierda - zona
    marcador = min(max(round(pct(temperatura)), 4), 96)
    tono, estado = _estado_lectura(temperatura, minimo, maximo)
    color = TONOS[tono][0]
    clase = f"cc-t-{tono}"
    fija = "table-layout:fixed;"
    celda = "padding:0;font-size:0;line-height:0;height:6px;mso-line-height-rule:exactly;"
    etiqueta = (
        f"padding-top:8px;font-family:{_FUENTE};font-size:11px;line-height:16px;font-weight:500;"
        f"color:{_FG3};white-space:nowrap;"
    )
    vacia = "padding:0;font-size:0;line-height:0;"
    return f"""
<p class="{clase}" style="margin:0;font-family:{_FUENTE};font-size:48px;line-height:52px;font-weight:700;letter-spacing:-1.5px;color:{color};text-align:center;white-space:nowrap;font-variant-numeric:tabular-nums;">{_num(temperatura)}<span style="font-size:20px;font-weight:500;letter-spacing:0;color:{_FG2};"> °C</span></p>
<p style="margin:8px 0 22px 0;font-family:{_FUENTE};font-size:13px;line-height:20px;text-align:center;"><strong class="{clase}" style="font-weight:600;color:{color};">{estado}</strong><span class="cc-fg3" style="color:{_FG3};"> · <span style="white-space:nowrap;">rango seguro {_num(minimo, "g")} a {_num(maximo, "g")} °C</span></span></p>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="{fija}">
  <tr>
    <td width="{marcador - 4}%" style="padding:0;font-size:14px;line-height:14px;width:{marcador - 4}%;">&nbsp;</td>
    <td align="center" width="8%" class="{clase}" style="padding:0 0 3px 0;width:8%;font-family:{_FUENTE};font-size:14px;line-height:14px;color:{color};mso-line-height-rule:exactly;">&#9660;</td>
    <td width="{100 - marcador - 4}%" style="padding:0;font-size:14px;line-height:14px;width:{100 - marcador - 4}%;">&nbsp;</td>
  </tr>
</table>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="{fija}">
  <tr>
    <td width="{izquierda}%" style="{celda}width:{izquierda}%;background-color:{_PISTA};border-radius:3px 0 0 3px;" bgcolor="{_PISTA}" class="cc-barra">&nbsp;</td>
    <td width="{zona}%" style="{celda}width:{zona}%;background-color:{_VERDE_RANGO};" bgcolor="{_VERDE_RANGO}">&nbsp;</td>
    <td width="{derecha}%" style="{celda}width:{derecha}%;background-color:{_PISTA};border-radius:0 3px 3px 0;" bgcolor="{_PISTA}" class="cc-barra">&nbsp;</td>
  </tr>
</table>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="{fija}">
  <tr>
    <td width="{izquierda}%" style="{vacia}width:{izquierda}%;">&nbsp;</td>
    <td width="{zona}%" class="cc-rango" style="padding:0;width:{zona}%;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>
      <td align="left" class="cc-fg3" style="{etiqueta}padding-left:4px;text-align:left;">{_num(minimo, "g")} °C</td>
      <td align="right" class="cc-fg3" style="{etiqueta}padding-right:4px;text-align:right;">{_num(maximo, "g")} °C</td>
    </tr></table></td>
    <td width="{derecha}%" style="{vacia}width:{derecha}%;">&nbsp;</td>
  </tr>
</table>"""


def _detalles(filas: list[tuple[str, str]]) -> str:
    """Lista de detalles con líneas finas: cada fila es la etiqueta en gris y, debajo, el valor.

    Apilada en todos los clientes, sin depender de media queries: Gmail con cuentas que no son de
    Google (y cualquier cliente que descarte el `<style>`) mostraría dos columnas estrechas y
    partiría las palabras a la mitad («ESP32_FA/RMACIA_01»)."""
    if not filas:
        return ""
    ultimo = len(filas) - 1
    celdas = []
    for i, (etiqueta, valor) in enumerate(filas):
        borde = "" if i == ultimo else f"border-bottom:1px solid {_BORDE_BLOQUE};"
        celdas.append(
            f'<tr><td class="cc-fg3" style="padding:12px 0 2px 0;font-family:{_FUENTE};font-size:12px;'
            f'line-height:16px;font-weight:500;color:{_FG3};text-align:left;">{_e(etiqueta)}</td></tr>'
            f'<tr><td class="cc-fg cc-linea" style="padding:0 0 12px 0;{borde}font-family:{_FUENTE};font-size:15px;'
            f'line-height:22px;font-weight:500;color:{_FG};text-align:left;overflow-wrap:anywhere;">{_e(valor)}</td></tr>'
        )
    return _bloque(_tabla("".join(celdas)), padding="6px 20px")


def _boton_azul(texto: str, url: str) -> str:
    """Botón a prueba de clientes: el fondo va también en la celda (Outlook ignora el del enlace).

    La etiqueta va dentro de `gmail-screen` > `gmail-diff`: en la app de Gmail (que ignora
    `prefers-color-scheme` e invierte los colores por su cuenta) las dos mezclas devuelven el texto
    a blanco sobre el azul. Fuera de Gmail el selector `u + .body` no coincide y no cambia nada."""
    return (
        f'<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:0;">'
        f'<tr><td align="center" bgcolor="{AZUL_MARCA}" class="cc-boton" style="border-radius:10px;'
        f'background-color:{AZUL_MARCA};mso-padding-alt:14px 24px;">'
        f'<a href="{_url_segura(url)}" target="_blank" class="cc-boton" '
        f'style="display:inline-block;padding:14px 24px;border-radius:10px;background-color:{AZUL_MARCA};'
        f"font-family:{_FUENTE};font-size:15px;line-height:20px;font-weight:600;color:#FFFFFF;"
        f'text-decoration:none;text-align:center;">'
        f'<span class="gmail-screen"><span class="gmail-diff">{_e(texto)}</span></span></a></td></tr></table>'
    )


def _boton_secundario(boton: Boton) -> str:
    """Botón grafito con borde, para cuando la acción principal es el código de verificación.

    Sin la mezcla de Gmail: si la app invierte el fondo, también invierte el texto y siguen contrastando."""
    return (
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:0;">'
        f'<tr><td align="center" bgcolor="{_BLOQUE}" class="cc-boton2" style="border-radius:10px;'
        f'background-color:{_BLOQUE};border:1px solid #3F3F46;mso-padding-alt:13px 22px;">'
        f'<a href="{_url_segura(boton.url)}" target="_blank" '
        f'style="display:inline-block;padding:13px 22px;border-radius:10px;background-color:{_BLOQUE};'
        f"font-family:{_FUENTE};font-size:15px;line-height:20px;font-weight:600;color:{_FG};"
        f'text-decoration:none;text-align:center;">{_e(boton.texto)}&nbsp;&rarr;</a></td></tr></table>'
    )


def _boton(boton: Boton, secundario: bool = False) -> str:
    if secundario:
        return _boton_secundario(boton)
    return _boton_azul(boton.texto, boton.url)


def _enlace_respaldo(boton: Boton) -> str:
    """Dirección del botón en texto. Muestra `boton.respaldo` si lo hay (misma pantalla, sin el
    fragmento que la rellena): el texto del enlace y su `href` siempre coinciden."""
    url = boton.respaldo or boton.url
    if _url_segura(url) == "#":
        return ""
    return _texto(
        f'¿El botón no funciona? Copia esta dirección en tu navegador:<br>'
        f'<a href="{_url_segura(url)}" target="_blank" class="cc-enlace" '
        f'style="color:{_FG2};text-decoration:underline;word-break:break-all;">{_e(url)}</a>',
        tam=12, alto=18, color=_FG3, clase="cc-fg3", margen="16px 0 0 0",
    )


ROTULO_CODIGO = "Tu código de verificación"


def bloque_codigo(codigo: str, vigencia: str | None = None, rotulo: str = ROTULO_CODIGO) -> str:
    """Las cifras forman un solo texto seleccionable; el lector de pantalla las deletrea."""
    deletreo = " ".join(codigo)
    instruccion = _texto("Selecciona el código y elige Copiar.", tam=13, alto=20, margen="10px 0 0 0", alinear="center")
    pie = (
        _texto(_e(vigencia), tam=12, alto=18, color=_FG3, clase="cc-fg3", margen="14px 0 0 0", alinear="center")
        if vigencia
        else ""
    )
    return _bloque(
        _texto(_e(rotulo), tam=11, alto=16, color=_FG3, clase="cc-fg3 cc-rotulo", margen="0 0 12px 0",
               alinear="center", peso=600, extra="letter-spacing:.6px;text-transform:uppercase;")
        + f'<p class="cc-codigo" role="text" aria-label="{_e(deletreo)}" style="margin:0;font-family:{_FUENTE};'
        f"font-size:38px;line-height:48px;font-weight:600;letter-spacing:5px;padding-left:5px;color:{_FG};"
        f"text-align:center;white-space:nowrap;font-variant-numeric:tabular-nums;font-feature-settings:'tnum' 1;"
        f'-webkit-user-select:all;user-select:all;">{_e(codigo)}</p>'
        + instruccion
        + pie,
        padding="26px 16px 22px 16px",
    )


def _encabezado_estado(tono: str, etiqueta: str) -> str:
    """Alertas: píldora con punto, fondo tintado y borde del color del estado.
    Resto: punto del color del tono y la etiqueta en gris."""
    acento, suave = TONOS[tono]
    if tono in _TONOS_DE_ESTADO:
        return (
            '<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:0 0 18px 0;">'
            f'<tr><td class="cc-pildora-{tono}" bgcolor="{suave}" style="padding:5px 12px;background-color:{suave};'
            f"border:1px solid {_BORDE_PILDORA[tono]};border-radius:999px;font-family:{_FUENTE};font-size:12px;"
            f'line-height:18px;font-weight:600;color:{acento};">'
            f'<span class="cc-t-{tono}" style="color:{acento};">&#9679;&nbsp;{_e(etiqueta)}</span></td></tr></table>'
        )
    return _texto(
        f'<span class="cc-t-{tono}" style="color:{acento};">&#9679;</span>&nbsp;&nbsp;{_e(etiqueta)}',
        tam=13, alto=20, color=_FG2, clase="cc-fg2", margen="0 0 14px 0", peso=500,
    )


# ── Documento ────────────────────────────────────────────────────────────

_ESTILOS = f"""
  body, table, td, p, a {{ -webkit-text-size-adjust:100%; -ms-text-size-adjust:100%; }}
  a {{ color:{TONOS["info"][0]}; }}
  a[x-apple-data-detectors] {{ color:inherit !important; text-decoration:none !important; font-size:inherit !important; font-family:inherit !important; font-weight:inherit !important; line-height:inherit !important; }}
  u + .body a, #MessageViewBody a {{ color:inherit; text-decoration:none; font-size:inherit; font-family:inherit; font-weight:inherit; line-height:inherit; }}
  @media only screen and (max-width:600px) {{
    .cc-exterior {{ padding:20px 10px 32px 10px !important; }}
    .cc-cabecera {{ padding:0 6px 18px 6px !important; }}
    .cc-panel {{ padding:0 22px 28px 22px !important; }}
    .cc-contenido {{ padding:28px 0 0 0 !important; }}
    .cc-pie {{ padding:24px 6px 0 6px !important; }}
    .cc-titular {{ font-size:26px !important; line-height:32px !important; letter-spacing:-.5px !important; }}
    .cc-codigo {{ font-size:36px !important; letter-spacing:5px !important; padding-left:5px !important; }}
  }}
  @media only screen and (min-width:601px) {{
    .cc-panel {{ padding:0 40px 40px 40px !important; }}
    .cc-codigo {{ font-size:44px !important; line-height:52px !important; letter-spacing:8px !important; padding-left:8px !important; }}
    .cc-rotulo {{ letter-spacing:1.2px !important; }}
  }}
  u + .body .gmail-screen {{ background:#000; mix-blend-mode:screen; }}
  u + .body .gmail-diff {{ background:#000; mix-blend-mode:difference; }}
"""

# El diseño ya es oscuro: en modo oscuro solo se reafirman las superficies y los textos para que
# ningún cliente que respete `color-scheme` los «aclare» por su cuenta.
_ESTILOS_OSCURO = f"""
  :root {{ color-scheme:light dark; supported-color-schemes:light dark; }}
  @media (prefers-color-scheme: dark) {{
    .cc-fondo {{ background-color:{_PAGINA} !important; }}
    .cc-tarjeta {{ background-color:{_TARJETA} !important; }}
    .cc-bloque {{ background-color:{_BLOQUE} !important; }}
    .cc-fg, .cc-titular, .cc-codigo {{ color:{_FG} !important; }}
    .cc-fg2 {{ color:{_FG2} !important; }}
    .cc-fg3 {{ color:{_FG3} !important; }}
    .cc-enlace {{ color:{_FG2} !important; }}
  }}
"""


def renderizar(
    *,
    asunto: str,
    preheader: str,
    tono: str,
    etiqueta: str,
    titulo: str,
    parrafos: list[str],
    detalles: list[tuple[str, str]] | None = None,
    escala: str | None = None,
    boton: Boton | None = None,
    nota: str | None = None,
    pie: str | None = None,
    codigo: str | None = None,
    codigo_vigencia: str | None = None,
    logo_url: str | None = None,
    codigo_rotulo: str = ROTULO_CODIGO,
) -> str:
    """Devuelve el documento HTML completo. `escala` es HTML ya generado por `escala_termica`.
    `codigo`: código de verificación de un solo uso, mostrado en grande (ver `bloque_codigo`).
    `codigo_rotulo`: rótulo sobre el código («Tu código de activación» en la activación).
    `logo_url`: icono de marca (https o ruta relativa de vista previa); sin él, marca con celdas."""
    if tono not in TONOS:
        raise KeyError(tono)
    src = _src_logo(logo_url)
    ultimo = len(parrafos) - 1
    cuerpo_parrafos = "".join(
        _texto(_e(p), margen=f"0 0 {28 if i == ultimo else 14}px 0") for i, p in enumerate(parrafos)
    )
    bloque_escala = _bloque(escala, padding="26px 16px 20px 16px") if escala else ""
    # Con código, la acción principal es el código: el botón de la página pasa a secundario.
    bloque_boton = (
        _boton(boton, secundario=bool(codigo)) + _enlace_respaldo(boton) if boton else ""
    )
    bloque_nota = (
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin:28px 0 0 0;">'
        f'<tr><td class="cc-linea" style="padding:20px 0 0 0;border-top:1px solid {_BORDE};">'
        f'{_texto(_e(nota), tam=13, alto=20, color=_FG3, clase="cc-fg3")}</td></tr></table>'
        if nota
        else ""
    )
    # Pie que invita a responder: un «no respondas» (no-reply) resta confianza ante los filtros de spam.
    texto_pie = pie or (
        "Recibes este mensaje porque eres parte del equipo de ColdCare. "
        "¿Dudas? Responde a este correo o habla con el administrador de tu farmacia."
    )
    # Sin relleno de caracteres invisibles tras el preheader: es una técnica de
    # correo masivo que los filtros de spam penalizan en remitentes nuevos.
    relleno = ""
    return f"""<!DOCTYPE html>
{_ATRIBUCION}
<html lang="es" dir="ltr" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta name="x-apple-disable-message-reformatting">
<meta name="format-detection" content="telephone=no,address=no,email=no,date=no,url=no">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{_e(asunto)}</title>
<style>{_ESTILOS}</style>
<style>{_ESTILOS_OSCURO}</style>
</head>
<body class="cc-fondo body" bgcolor="{_PAGINA}" style="margin:0;padding:0;width:100%;background-color:{_PAGINA};">
<span style="display:none !important;visibility:hidden;mso-hide:all;font-size:1px;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;">{_e(preheader)}{relleno}</span>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" class="cc-fondo" bgcolor="{_PAGINA}" style="background-color:{_PAGINA};">
  <tr><td align="center" class="cc-exterior" style="padding:44px 16px 48px 16px;">
    <!--[if mso]><table role="presentation" width="600" align="center" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
    <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" class="cc-contenedor" style="width:100%;max-width:600px;">
      <tr><td class="cc-cabecera" style="padding:0 8px 22px 8px;">{_cabecera(src)}</td></tr>
      <tr><td style="padding:0;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" class="cc-tarjeta" bgcolor="{_TARJETA}" style="border-collapse:separate;background-color:{_TARJETA};border:1px solid {_BORDE};border-radius:16px;">
          <tr><td class="cc-panel cc-tarjeta" bgcolor="{_TARJETA}" style="padding:0 32px 36px 32px;background-color:{_TARJETA};background-image:{_RESPLANDOR};border-radius:16px;text-align:left;">
            {_filo_de_luz()}
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
              <tr><td class="cc-contenido" style="padding:36px 0 0 0;text-align:left;">
                {_encabezado_estado(tono, etiqueta)}
                <h1 class="cc-titular" style="margin:0 0 14px 0;font-family:{_FUENTE};font-size:30px;line-height:36px;font-weight:700;letter-spacing:-.7px;color:{_FG};text-align:left;">{_e(titulo)}</h1>
                {cuerpo_parrafos}
                {bloque_codigo(codigo, codigo_vigencia, codigo_rotulo) if codigo else ""}
                {bloque_escala}
                {_detalles(detalles or [])}
                {bloque_boton}
                {bloque_nota}
              </td></tr>
            </table>
          </td></tr>
        </table>
      </td></tr>
      <tr><td class="cc-pie" style="padding:28px 8px 0 8px;text-align:left;">
        {_texto(f"{MARCA} · {LEMA}", tam=12, alto=18, color=_FG2, clase="cc-fg2", margen="0 0 8px 0", peso=600)}
        {_texto(_e(texto_pie), tam=12, alto=18, color=_FG3, clase="cc-fg3", extra="max-width:440px;")}
      </td></tr>
    </table>
    <!--[if mso]></td></tr></table><![endif]-->
  </td></tr>
</table>
</body>
</html>"""
