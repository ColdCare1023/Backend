"""Plantilla HTML de los correos de ColdCare.

Un solo diseño para todos los avisos (cuenta creada, contraseña, códigos, alertas), copiado de la
plantilla «Barebone» de React Email (https://github.com/resend/react-email, apps/demo/emails):
página gris clara, contenedor blanco, fila de cabecera con el logo a la izquierda y el nombre a la
derecha, panel gris redondeado con el icono centrado, titular grande, texto corto centrado, botón
en píldora y una nota pequeña en gris. Sobre esa base se añadieron los bloques propios de ColdCare:
código de verificación, lectura térmica con la barra del rango 2-8 °C, lista de detalles y la
píldora de estado de las alertas.

Licencia de la plantilla base (MIT):
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
  externo); el bloque `<style>` solo lleva media queries y el modo oscuro;
- 600 px de ancho máximo, fluida hasta 375 px;
- sin fuentes remotas; la única imagen es el icono de marca y solo si se pasa `logo_url`
  (sin él se dibuja una marca con celdas, y con imágenes bloqueadas el `alt` va estilado);
- colores sólidos con contraste AA; el estado nunca depende solo del color (va escrito);
- todo texto dinámico pasa por `html.escape`.

El correo siempre lleva además la versión de texto plano (ver `MensajeCorreo.cuerpo`).
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from urllib.parse import urlsplit

# Tono → (color de estado con contraste AA sobre blanco, fondo suave de la píldora).
TONOS: dict[str, tuple[str, str]] = {
    "critico": ("#B42318", "#FEF3F2"),
    "aviso": ("#B54708", "#FFFAEB"),
    "ok": ("#067647", "#ECFDF3"),
    "info": ("#0B5CFF", "#EEF4FF"),
    # Correos de cuenta con código (login, recuperación, activación): azul de marca.
    "marca": ("#0B5CFF", "#EEF4FF"),
}
# Solo las alertas llevan píldora de color; el resto, una línea gris como en Barebone.
_TONOS_DE_ESTADO = ("critico", "aviso")

# Paleta ColdCare: azul primario (botón y acentos) y azul marino (icono y código).
AZUL_MARCA = "#0B5CFF"
AZUL_MARINO = "#0F172A"

# Paleta neutra de Barebone (theme.ts). FG3 se oscureció de #7B7D81 a #686B70 para pasar AA sobre el gris.
_BG = "#FFFFFF"
_BG2 = "#F3F4F6"
_FG = "#14171E"
_FG2 = "#43454B"
_FG3 = "#686B70"
_LINEA = "#E4E4E7"
_VERDE_RANGO = "#12B76A"

MARCA = "ColdCare"
LEMA = "Monitoreo de cadena de frío"
RUTA_LOGO = "/email/coldcare-icon-96.png"

_FUENTE = "Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
_FUENTE_MONO = "'SFMono-Regular',Menlo,Consolas,'Liberation Mono','Courier New',monospace"

_ATRIBUCION = (
    "<!-- Diseño basado en la plantilla «Barebone» de React Email "
    "(github.com/resend/react-email). MIT License. Copyright 2024 Plus Five Five, Inc. -->"
)


@dataclass(frozen=True, slots=True)
class Boton:
    texto: str
    url: str


def _e(valor: object) -> str:
    return escape(str(valor), quote=True)


def _url_segura(url: str) -> str:
    """Solo http(s): un enlace `javascript:` o `data:` no debe llegar nunca a un href."""
    return _e(url) if url.lower().startswith(("http://", "https://")) else "#"


def url_logo(frontend_url: str | None) -> str | None:
    """URL pública del icono de marca servido por el frontend, solo si es https.

    Acepta el origen del frontend o cualquier página suya: se queda con el esquema y el host."""
    if not frontend_url or not frontend_url.lower().startswith("https://"):
        return None
    partes = urlsplit(frontend_url)
    return f"https://{partes.netloc}{RUTA_LOGO}" if partes.netloc else None


def _src_logo(logo_url: str | None) -> str | None:
    """https absoluto o ruta relativa (vista previa local). Nada de `javascript:`, `data:` ni `//host`."""
    if not logo_url:
        return None
    if logo_url.lower().startswith("https://"):
        return _e(logo_url)
    if ":" in logo_url or logo_url.startswith("//"):
        return None
    return _e(logo_url)


def _texto(contenido: str, *, tam: int = 16, alto: int = 24, color: str = _FG2, clase: str = "cc-fg2",
           margen: str = "0", ancho: int | None = None, alinear: str = "center", peso: int = 400,
           extra: str = "") -> str:
    limite = f"max-width:{ancho}px;" if ancho else ""
    return (
        f'<p class="{clase}" style="margin:{margen};{limite}font-family:{_FUENTE};font-size:{tam}px;'
        f'line-height:{alto}px;font-weight:{peso};color:{color};text-align:{alinear};{extra}">{contenido}</p>'
    )


def _tabla(contenido: str, estilo: str = "", clase: str = "", atributos: str = "") -> str:
    cls = f' class="{clase}"' if clase else ""
    return (
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"{cls}{atributos} '
        f'style="border-collapse:separate;{estilo}">{contenido}</table>'
    )


def _tarjeta(contenido: str, padding: str = "20px 20px") -> str:
    """Celda blanca redondeada dentro del panel gris (para el código, la lectura y los detalles)."""
    return _tabla(
        f'<tr><td class="cc-tarjeta" bgcolor="{_BG}" style="padding:{padding};background-color:{_BG};'
        f'border:1px solid {_LINEA};border-radius:10px;">{contenido}</td></tr>',
        estilo="margin:0 0 24px 0;",
    )


# ── Marca ────────────────────────────────────────────────────────────────


def _marca_celdas(tam: int, centrado: bool = True) -> str:
    """Marca sin imagen: cuadrado azul marino redondeado con la «C» (si no hay `logo_url`)."""
    radio = round(tam * 0.26)
    letra = round(tam * 0.5)
    return (
        f'<table role="presentation" cellspacing="0" cellpadding="0" border="0" align="{"center" if centrado else "left"}"><tr>'
        f'<td align="center" valign="middle" width="{tam}" height="{tam}" bgcolor="{AZUL_MARINO}" '
        f'style="width:{tam}px;height:{tam}px;background-color:{AZUL_MARINO};border-radius:{radio}px;'
        f"border-bottom:{max(2, tam // 16)}px solid #38BDF8;font-family:{_FUENTE};font-size:{letra}px;"
        f'line-height:{tam}px;font-weight:700;color:#FFFFFF;mso-line-height-rule:exactly;">C</td>'
        "</tr></table>"
    )


def _logo(src: str | None, tam: int, alt: str, centrado: bool) -> str:
    if src is None:
        return _marca_celdas(tam, centrado)
    margen = "0 auto" if centrado else "0"
    # Con imágenes bloqueadas se ve el alt con la tipografía y el color de la marca.
    return (
        f'<img src="{src}" width="{tam}" height="{tam}" alt="{_e(alt)}" '
        f'style="display:block;margin:{margen};width:{tam}px;height:{tam}px;border:0;outline:none;'
        f"text-decoration:none;border-radius:{round(tam * 0.22)}px;font-family:{_FUENTE};font-size:13px;"
        f'line-height:{tam}px;font-weight:600;color:{AZUL_MARINO};text-align:center;">'
    )


def _cabecera(src: str | None) -> str:
    """Fila de Barebone: logo pequeño a la izquierda y el nombre en gris a la derecha."""
    return _tabla(
        "<tr>"
        f'<td width="50%" valign="middle" style="padding:7px 0;">{_logo(src, 24, "", centrado=False)}</td>'
        f'<td width="50%" align="right" valign="middle" style="padding:7px 0;">'
        f'{_texto(MARCA, tam=13, alto=20, color=_FG3, clase="cc-fg3", alinear="right", peso=500)}</td>'
        "</tr>",
        estilo="margin:0 0 12px 0;",
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
    return texto.replace("-", "\u2212")


def formato_temperatura(temperatura: float) -> str:
    """«-127.0 °C» con U+2212: lectura con un decimal y signo menos tipográfico, para el HTML."""
    return f"{_num(temperatura)} °C"


def escala_termica(temperatura: float, minimo: float = 2.0, maximo: float = 8.0) -> str:
    """Lectura destacada + barra con la zona segura (2-8 °C) en verde y un marcador en la lectura.

    El estado va escrito («Fuera de rango», «Cerca del límite», «Dentro de rango»): el color solo lo
    refuerza. El eje es fijo (6 °C a cada lado del rango, -4 a 14 °C), así la zona verde ocupa siempre
    el mismo tercio de la barra; una lectura fuera del eje (p. ej. -127 °C o 85 °C, valores de un
    sensor desconectado) deja el marcador pegado al extremo y el valor exacto queda en el número grande.

    La barra, el marcador y las etiquetas van en tres tablas con `table-layout:fixed` y los mismos
    anchos: el texto de las etiquetas no puede ensanchar las celdas de la barra. Bajo el tramo verde
    no va texto (en 320 px no cabe y chocaba con «8 °C»): la línea de estado ya dice «rango seguro»."""
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
    celda = "padding:0;font-size:0;line-height:0;height:8px;mso-line-height-rule:exactly;"
    etiqueta = f"padding:6px 0 0 0;font-family:{_FUENTE};font-size:12px;line-height:16px;white-space:nowrap;"
    return f"""
<p class="{clase}" style="margin:0;font-family:{_FUENTE};font-size:44px;line-height:48px;font-weight:600;letter-spacing:-1px;color:{color};text-align:center;white-space:nowrap;">{_num(temperatura)}<span style="font-size:20px;font-weight:500;letter-spacing:0;"> °C</span></p>
<p style="margin:6px 0 18px 0;font-family:{_FUENTE};font-size:13px;line-height:20px;text-align:center;"><strong class="{clase}" style="font-weight:600;color:{color};">{estado}</strong><span class="cc-fg3" style="color:{_FG3};"> · rango seguro {_num(minimo, "g")} a {_num(maximo, "g")} °C</span></p>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="{fija}">
  <tr>
    <td width="{marcador - 3}%" style="padding:0;font-size:10px;line-height:12px;width:{marcador - 3}%;">&nbsp;</td>
    <td align="center" width="6%" class="{clase}" style="padding:0 0 2px 0;width:6%;font-family:{_FUENTE};font-size:10px;line-height:12px;color:{color};">&#9660;</td>
    <td width="{100 - marcador - 3}%" style="padding:0;font-size:10px;line-height:12px;width:{100 - marcador - 3}%;">&nbsp;</td>
  </tr>
</table>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="{fija}">
  <tr>
    <td width="{izquierda}%" style="{celda}width:{izquierda}%;background-color:#E4E4E7;border-radius:4px 0 0 4px;" class="cc-barra">&nbsp;</td>
    <td width="{zona}%" style="{celda}width:{zona}%;background-color:{_VERDE_RANGO};">&nbsp;</td>
    <td width="{derecha}%" style="{celda}width:{derecha}%;background-color:#E4E4E7;border-radius:0 4px 4px 0;" class="cc-barra">&nbsp;</td>
  </tr>
</table>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="{fija}">
  <tr>
    <td width="{izquierda}%" align="right" class="cc-fg3" style="{etiqueta}width:{izquierda}%;padding-right:6px;color:{_FG3};text-align:right;">{_num(minimo, "g")} °C</td>
    <td width="{zona}%" style="{etiqueta}width:{zona}%;">&nbsp;</td>
    <td width="{derecha}%" align="left" class="cc-fg3" style="{etiqueta}width:{derecha}%;padding-left:6px;color:{_FG3};text-align:left;">{_num(maximo, "g")} °C</td>
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
        borde = "" if i == ultimo else f"border-bottom:1px solid {_LINEA};"
        celdas.append(
            f'<tr><td class="cc-fg3" style="padding:12px 0 2px 0;font-family:{_FUENTE};font-size:13px;'
            f'line-height:18px;color:{_FG3};text-align:left;">{_e(etiqueta)}</td></tr>'
            f'<tr><td class="cc-fg cc-linea" style="padding:0 0 12px 0;{borde}font-family:{_FUENTE};font-size:15px;'
            f'line-height:22px;font-weight:500;color:{_FG};text-align:left;overflow-wrap:anywhere;">{_e(valor)}</td></tr>'
        )
    return _tarjeta(_tabla("".join(celdas)), padding="4px 20px")


def _boton(boton: Boton) -> str:
    """Botón a prueba de clientes (Barebone: píldora 16px, relleno 16 x 28): el fondo va en la celda para Outlook.

    La etiqueta va dentro de `gmail-screen` > `gmail-diff`: en Gmail (que ignora `prefers-color-scheme`
    e invierte los colores por su cuenta) las dos mezclas devuelven el texto a blanco sobre el azul.
    Fuera de Gmail el selector `u + .body` no coincide y no cambia nada."""
    return (
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0" align="center" style="margin:0 auto 24px auto;">'
        f'<tr><td align="center" bgcolor="{AZUL_MARCA}" class="cc-boton" style="border-radius:8px;background-color:{AZUL_MARCA};mso-padding-alt:16px 28px;">'
        f'<a href="{_url_segura(boton.url)}" target="_blank" class="cc-boton" '
        f'style="display:inline-block;padding:16px 28px;border-radius:8px;background-color:{AZUL_MARCA};'
        f"font-family:{_FUENTE};font-size:16px;line-height:24px;font-weight:600;color:#FFFFFF;"
        f'text-decoration:none;text-align:center;">'
        f'<span class="gmail-screen"><span class="gmail-diff">{_e(boton.texto)}</span></span></a></td></tr></table>'
    )


def _enlace_respaldo(boton: Boton) -> str:
    if _url_segura(boton.url) == "#":
        return ""
    return _texto(
        f'¿El botón no funciona? Copia esta dirección en tu navegador:<br>'
        f'<a href="{_url_segura(boton.url)}" target="_blank" class="cc-enlace" '
        f'style="color:{_FG2};text-decoration:underline;word-break:break-all;">{_e(boton.url)}</a>',
        tam=12, alto=18, color=_FG3, clase="cc-fg3", margen="16px auto 0 auto", ancho=420,
    )


def bloque_codigo(codigo: str, vigencia: str | None = None) -> str:
    """Código de un solo uso en grande y monoespaciado, en una tarjeta blanca dentro del panel.

    Sin imágenes: el código es texto seleccionable (se puede copiar y lo lee un lector de
    pantalla). El `aria-label` lo deletrea dígito a dígito. En línea va a 34 px con 8 px de espaciado:
    así cabe en 360 px aunque el cliente descarte el `<style>` (a 38 px se salía y la página se desplazaba)."""
    deletreo = " ".join(codigo)
    pie = (
        _texto(_e(vigencia), tam=13, alto=20, color=_FG3, clase="cc-fg3", margen="8px 0 0 0")
        if vigencia
        else ""
    )
    return _tarjeta(
        _texto("Tu código de verificación", tam=12, alto=16, color=AZUL_MARCA, clase="cc-t-marca",
               margen="0 0 10px 0", peso=600, extra="letter-spacing:.6px;text-transform:uppercase;")
        + f'<p class="cc-codigo" role="text" aria-label="{_e(deletreo)}" style="margin:0;font-family:{_FUENTE_MONO};'
        f"font-size:34px;line-height:44px;font-weight:700;letter-spacing:8px;padding-left:8px;color:{AZUL_MARINO};"
        f'text-align:center;-webkit-user-select:all;user-select:all;">{_e(codigo)}</p>'
        + pie,
        padding="22px 16px 20px 16px",
    )


def _encabezado_estado(tono: str, etiqueta: str) -> str:
    """Alertas: píldora pequeña con punto y texto del estado. Resto: línea gris de Barebone."""
    if tono in _TONOS_DE_ESTADO:
        acento, suave = TONOS[tono]
        return (
            '<table role="presentation" cellspacing="0" cellpadding="0" border="0" align="center" style="margin:0 auto 14px auto;">'
            f'<tr><td class="cc-pildora-{tono}" bgcolor="{suave}" style="padding:4px 12px;background-color:{suave};border-radius:999px;'
            f'font-family:{_FUENTE};font-size:12px;line-height:18px;font-weight:600;color:{acento};">'
            f'<span class="cc-t-{tono}" style="color:{acento};">&#9679;&nbsp;{_e(etiqueta)}</span></td></tr></table>'
        )
    return _texto(_e(etiqueta), tam=13, alto=20, color=_FG3, clase="cc-fg3", margen="0 0 10px 0", peso=500)


# ── Documento ────────────────────────────────────────────────────────────

_ESTILOS = f"""
  body, table, td, p, a {{ -webkit-text-size-adjust:100%; -ms-text-size-adjust:100%; }}
  a {{ color:{AZUL_MARCA}; }}
  a[x-apple-data-detectors] {{ color:inherit !important; text-decoration:none !important; font-size:inherit !important; font-family:inherit !important; font-weight:inherit !important; line-height:inherit !important; }}
  u + .body a, #MessageViewBody a {{ color:inherit; text-decoration:none; font-size:inherit; font-family:inherit; font-weight:inherit; line-height:inherit; }}
  @media only screen and (max-width:600px) {{
    .cc-exterior {{ padding:0 !important; }}
    .cc-marco {{ padding:12px 8px 8px 8px !important; }}
    .cc-cabecera {{ padding:0 16px !important; }}
    .cc-panel {{ padding:44px 20px 40px 20px !important; }}
    .cc-titular {{ font-size:24px !important; line-height:31px !important; }}
    .cc-codigo {{ font-size:32px !important; letter-spacing:7px !important; padding-left:7px !important; }}
  }}
  u + .body .gmail-screen {{ background:#000; mix-blend-mode:screen; }}
  u + .body .gmail-diff {{ background:#000; mix-blend-mode:difference; }}
"""

_ESTILOS_OSCURO = """
  @media (prefers-color-scheme: dark) {
    .cc-fondo { background-color:#0B0C0F !important; }
    .cc-contenedor { background-color:#16181C !important; }
    .cc-panel { background-color:#202227 !important; }
    .cc-tarjeta { background-color:#2A2D33 !important; border-color:#3A3D44 !important; }
    .cc-fg, .cc-titular { color:#F5F6F7 !important; }
    .cc-fg2 { color:#C7C9CE !important; }
    .cc-fg3 { color:#9EA1A8 !important; }
    .cc-enlace { color:#C7C9CE !important; }
    .cc-linea { border-color:#3A3D44 !important; }
    .cc-barra { background-color:#454951 !important; }
    .cc-codigo { color:#FFFFFF !important; }
    .cc-t-critico { color:#FDA29B !important; }
    .cc-t-aviso { color:#FEC84B !important; }
    .cc-t-ok { color:#75E0A7 !important; }
    .cc-t-info, .cc-t-marca { color:#8AB4FF !important; }
    .cc-pildora-critico { background-color:#3A1512 !important; }
    .cc-pildora-aviso { background-color:#3A2A0A !important; }
  }
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
) -> str:
    """Devuelve el documento HTML completo. `escala` es HTML ya generado por `escala_termica`.
    `codigo`: código de verificación de un solo uso, mostrado en grande (ver `bloque_codigo`).
    `logo_url`: icono de marca (https o ruta relativa de vista previa); sin él, marca con celdas."""
    if tono not in TONOS:
        raise KeyError(tono)
    src = _src_logo(logo_url)
    ultimo = len(parrafos) - 1
    cuerpo_parrafos = "".join(
        _texto(_e(p), margen=f"0 auto {28 if i == ultimo else 14}px auto", ancho=420)
        for i, p in enumerate(parrafos)
    )
    bloque_escala = _tarjeta(escala, padding="22px 20px 18px 20px") if escala else ""
    bloque_nota = (
        _texto(_e(nota), tam=13, alto=20, color=_FG3, clase="cc-fg3", margen="8px auto 0 auto", ancho=420)
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
<body class="cc-fondo body" style="margin:0;padding:0;width:100%;background-color:{_BG2};">
<span style="display:none !important;visibility:hidden;mso-hide:all;font-size:1px;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;">{_e(preheader)}{relleno}</span>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" class="cc-fondo" bgcolor="{_BG2}" style="background-color:{_BG2};">
  <tr><td align="center" class="cc-exterior" style="padding:32px 12px 32px 12px;">
    <!--[if mso]><table role="presentation" width="600" align="center" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
    <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" class="cc-contenedor" bgcolor="{_BG}" style="width:100%;max-width:600px;background-color:{_BG};">
      <tr><td class="cc-marco" style="padding:16px 24px 8px 24px;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
          <tr><td class="cc-cabecera" style="padding:0 24px;">{_cabecera(src)}</td></tr>
        </table>
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
          <tr><td class="cc-panel" bgcolor="{_BG2}" align="center" style="padding:56px 32px 52px 32px;background-color:{_BG2};border-radius:8px;text-align:center;">
            <div style="margin:0 0 20px 0;">{_logo(src, 48, MARCA, centrado=True)}</div>
            {_encabezado_estado(tono, etiqueta)}
            <h1 class="cc-titular" style="margin:0 auto 14px auto;max-width:460px;font-family:{_FUENTE};font-size:28px;line-height:36px;font-weight:600;letter-spacing:-.3px;color:{_FG};text-align:center;">{_e(titulo)}</h1>
            {cuerpo_parrafos}
            {bloque_codigo(codigo, codigo_vigencia) if codigo else ""}
            {bloque_escala}
            {_detalles(detalles or [])}
            {_boton(boton) if boton else ""}
            {bloque_nota}
            {_enlace_respaldo(boton) if boton else ""}
          </td></tr>
        </table>
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
          <tr><td align="center" style="padding:36px 24px 32px 24px;text-align:center;">
            {_texto(f"{MARCA} · {LEMA}", tam=13, alto=20, color=_FG3, clase="cc-fg3", margen="0 auto 10px auto", peso=500)}
            {_texto(_e(texto_pie), tam=11, alto=17, color=_FG3, clase="cc-fg3", margen="0 auto", ancho=360)}
          </td></tr>
        </table>
      </td></tr>
    </table>
    <!--[if mso]></td></tr></table><![endif]-->
  </td></tr>
</table>
</body>
</html>"""
