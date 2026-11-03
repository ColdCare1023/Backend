"""Plantilla HTML de los correos de ColdCare.

Un solo diseño para todos los avisos (cuenta creada, contraseña, alertas): basado en la maqueta MIT «basic-full» de Postmark
(github.com/ActiveCampaign/postmark-templates): fondo gris, marca centrada, tarjeta blanca, caja de
atributos gris, botón con borde y enlace de respaldo. Se añadió la lectura térmica y el tono de estado. Está hecha para clientes de correo, no para navegadores:

- tablas y estilos en línea (Gmail, Outlook y Apple Mail ignoran o recortan el CSS externo);
- sin imágenes ni fuentes remotas: no hay nada que bloquear ni que rastree la apertura;
- 600 px de ancho máximo, se adapta a pantallas estrechas;
- modo oscuro con `prefers-color-scheme` donde el cliente lo respeta;
- todo texto dinámico pasa por `html.escape`.

El correo siempre lleva además la versión de texto plano (ver `MensajeCorreo.cuerpo`).
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

# Tono → (color de acento, fondo suave, símbolo del icono)
TONOS: dict[str, tuple[str, str]] = {
    "critico": ("#e5382b", "#fdecea"),
    "aviso": ("#e07b00", "#fff3df"),
    "ok": ("#0e9f6e", "#e3f7ee"),
    "info": ("#2563eb", "#e8f0fe"),
}
_SIMBOLO = {"critico": "!", "aviso": "!", "ok": "&#10003;", "info": "i"}

MARCA = "ColdCare"
LEMA = "Monitoreo de cadena de frío"
_HIELO = "#38bdf8"

_FUENTE = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif"


@dataclass(frozen=True, slots=True)
class Boton:
    texto: str
    url: str


def _e(valor: object) -> str:
    return escape(str(valor), quote=True)


def _url_segura(url: str) -> str:
    """Solo http(s): un enlace `javascript:` o `data:` no debe llegar nunca a un href."""
    return _e(url) if url.lower().startswith(("http://", "https://")) else "#"


def escala_termica(temperatura: float, minimo: float = 2.0, maximo: float = 8.0) -> str:
    """Lectura destacada + barra con la zona segura (2-8 °C) en verde y un marcador en la lectura.

    El eje se amplía si la lectura cae fuera de [-2, 14] para que el marcador nunca quede cortado."""
    inferior = min(minimo - 4.0, temperatura - 1.0)
    superior = max(maximo + 6.0, temperatura + 1.0)
    ancho = superior - inferior

    def pct(valor: float) -> float:
        return (valor - inferior) / ancho * 100.0

    izquierda = round(pct(minimo))
    zona = round(pct(maximo)) - izquierda
    derecha = 100 - izquierda - zona
    marcador = min(max(round(pct(temperatura)), 3), 97)
    fuera = temperatura < minimo or temperatura > maximo
    color = TONOS["critico"][0] if fuera else TONOS["ok"][0]
    estado = "Fuera de rango" if fuera else "Dentro de rango"
    clase = "cc-t-critico" if fuera else "cc-t-ok"
    celda = "padding:0;font-size:0;line-height:0;height:8px;"
    return f"""
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
  <tr>
    <td class="{clase}" style="padding:0;font-family:{_FUENTE};font-size:34px;line-height:38px;font-weight:600;color:{color};letter-spacing:-.5px;">{temperatura:.1f}<span style="font-size:16px;font-weight:500;"> °C</span></td>
    <td align="right" valign="bottom" class="{clase}" style="padding:0 0 4px 0;font-family:{_FUENTE};font-size:12px;font-weight:600;color:{color};">{estado}</td>
  </tr>
</table>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin-top:10px;">
  <tr>
    <td style="padding:0;width:{marcador - 3}%;">&nbsp;</td>
    <td align="center" class="{clase}" style="padding:0;width:6%;font-family:{_FUENTE};font-size:10px;line-height:10px;color:{color};">&#9660;</td>
    <td style="padding:0;">&nbsp;</td>
  </tr>
</table>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border-radius:4px;overflow:hidden;">
  <tr>
    <td width="{izquierda}%" style="{celda}background-color:#a5d6ff;">&nbsp;</td>
    <td width="{zona}%" style="{celda}background-color:#12b76a;">&nbsp;</td>
    <td width="{derecha}%" style="{celda}background-color:#ffc680;">&nbsp;</td>
  </tr>
</table>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin-top:6px;">
  <tr>
    <td width="{izquierda}%" align="right" class="cc-muted" style="padding:0;font-family:{_FUENTE};font-size:11px;color:#59636e;">{minimo:g} °C</td>
    <td width="{zona}%" align="center" style="padding:0;font-family:{_FUENTE};font-size:11px;color:#0e9f6e;font-weight:600;">rango seguro</td>
    <td width="{derecha}%" align="left" class="cc-muted" style="padding:0;font-family:{_FUENTE};font-size:11px;color:#59636e;">{maximo:g} °C</td>
  </tr>
</table>"""


def _detalles(filas: list[tuple[str, str]]) -> str:
    """Caja gris de atributos (como `attributes` de Postmark): etiqueta en negrita y valor."""
    if not filas:
        return ""
    celdas = "".join(
        f'<tr><td class="cc-texto" style="padding:0 0 8px 0;font-family:{_FUENTE};font-size:15px;line-height:22px;color:#51545e;">'
        f'<strong style="color:#333333;" class="cc-texto">{_e(etiqueta)}:</strong> {_e(valor)}</td></tr>'
        for etiqueta, valor in filas
    )
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin:0 0 21px 0;">'
        '<tr><td class="cc-gris" bgcolor="#f4f4f7" style="padding:16px 16px 8px 16px;background-color:#f4f4f7;">'
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">{celdas}</table>'
        "</td></tr></table>"
    )


def _boton(boton: Boton, color: str) -> str:
    """Botón con borde (técnica de Postmark/Litmus): el color va también en el borde para Outlook."""
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin:28px auto;">'
        f'<tr><td align="center"><a href="{_url_segura(boton.url)}" target="_blank" '
        f'style="display:inline-block;background-color:{color};border:solid {color};border-width:10px 18px;border-radius:3px;'
        f"box-shadow:0 2px 3px rgba(0,0,0,.16);font-family:{_FUENTE};font-size:16px;font-weight:700;color:#ffffff;"
        f'text-decoration:none;">{_e(boton.texto)}</a></td></tr></table>'
    )


def _enlace_respaldo(boton: Boton) -> str:
    if _url_segura(boton.url) == "#":
        return ""
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" class="cc-linea" '
        'style="margin-top:25px;border-top:1px solid #eaeaec;"><tr><td style="padding-top:20px;">'
        f'<p class="cc-muted" style="margin:0 0 6px 0;font-family:{_FUENTE};font-size:13px;line-height:21px;color:#6b6e76;">'
        "Si tienes problemas con el botón, copia y pega esta dirección en tu navegador:</p>"
        f'<p class="cc-muted" style="margin:0;font-family:{_FUENTE};font-size:13px;line-height:21px;color:#6b6e76;word-break:break-all;">'
        f"{_e(boton.url)}</p></td></tr></table>"
    )


def _marca() -> str:
    """Logotipo hecho con una celda (sin imagen): cuadrado azul hielo con la «C» y el nombre."""
    return (
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0" align="center"><tr>'
        f'<td align="center" width="30" height="30" style="width:30px;height:30px;background-color:{_HIELO};'
        f"border-radius:8px;font-family:{_FUENTE};font-size:17px;font-weight:800;color:#06202f;\">C</td>"
        f'<td class="cc-marca" style="padding-left:10px;font-family:{_FUENTE};font-size:18px;font-weight:700;color:#a8aaaf;">{MARCA}</td>'
        "</tr></table>"
    )


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
) -> str:
    """Devuelve el documento HTML completo. `escala` es HTML ya generado por `escala_termica`."""
    acento, suave = TONOS[tono]
    cuerpo_parrafos = "".join(
        f'<p class="cc-texto" style="margin:.4em 0 1.1875em 0;font-family:{_FUENTE};font-size:16px;line-height:26px;'
        f'color:#51545e;">{_e(p)}</p>'
        for p in parrafos
    )
    bloque_escala = (
        f'<div class="cc-gris" style="margin:0 0 21px 0;padding:18px 16px 14px 16px;background-color:#f4f4f7;">{escala}</div>'
        if escala
        else ""
    )
    bloque_nota = (
        f'<p class="cc-muted" style="margin:14px 0 0 0;font-family:{_FUENTE};font-size:14px;line-height:23px;color:#6b6e76;">{_e(nota)}</p>'
        if nota
        else ""
    )
    texto_pie = pie or "Recibes este mensaje porque eres parte del equipo de ColdCare. Es un aviso automático: no respondas a este correo."
    chip = (
        f'<span class="cc-t-{tono}" style="display:inline-block;padding:4px 10px;background-color:{suave};border-radius:3px;'
        f'font-family:{_FUENTE};font-size:12px;font-weight:700;letter-spacing:.8px;color:{acento};text-transform:uppercase;">{_e(etiqueta)}</span>'
    )
    return f"""<!DOCTYPE html>
<html lang="es" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{_e(asunto)}</title>
<style>
  @media only screen and (max-width:600px) {{ .cc-tarjeta {{ width:100% !important; }} .cc-celda {{ padding:24px 20px !important; }} }}
  @media (prefers-color-scheme: dark) {{
    .cc-fondo {{ background-color:#333333 !important; }}
    .cc-tarjeta {{ background-color:#222222 !important; }}
    .cc-texto, .cc-texto strong {{ color:#ffffff !important; }}
    .cc-muted, .cc-marca {{ color:#b0b3ba !important; }}
    .cc-gris {{ background-color:#2d2d2d !important; }}
    .cc-linea {{ border-color:#444444 !important; }}
    .cc-t-critico {{ color:#ff8b80 !important; }}
    .cc-t-aviso {{ color:#ffb866 !important; }}
    .cc-t-ok {{ color:#4cd694 !important; }}
    .cc-t-info {{ color:#8ab4ff !important; }}
  }}
</style>
</head>
<body class="cc-fondo" style="margin:0;padding:0;width:100%;background-color:#f4f4f7;-webkit-text-size-adjust:none;">
<span style="display:none !important;visibility:hidden;mso-hide:all;font-size:1px;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;">{_e(preheader)}</span>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" class="cc-fondo" style="background-color:#f4f4f7;">
  <tr><td align="center" style="padding:0 12px 32px 12px;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
      <tr><td align="center" style="padding:28px 0 24px 0;">{_marca()}</td></tr>
      <tr><td align="center">
        <table role="presentation" width="570" cellspacing="0" cellpadding="0" border="0" class="cc-tarjeta" style="width:570px;max-width:570px;background-color:#ffffff;border-top:4px solid {acento};border-radius:4px;box-shadow:0 1px 3px rgba(0,0,0,.08);">
          <tr><td class="cc-celda" style="padding:35px;">
            {chip}
            <h1 class="cc-texto" style="margin:14px 0 14px 0;font-family:{_FUENTE};font-size:24px;line-height:32px;font-weight:700;color:#333333;text-align:left;">{_e(titulo)}</h1>
            {cuerpo_parrafos}
            {bloque_escala}
            {_detalles(detalles or [])}
            {_boton(boton, acento) if boton else ""}
            {bloque_nota}
            {_enlace_respaldo(boton) if boton else ""}
          </td></tr>
        </table>
      </td></tr>
      <tr><td align="center" style="padding:24px 0 0 0;">
        <table role="presentation" width="570" cellspacing="0" cellpadding="0" border="0" style="width:570px;max-width:570px;">
          <tr><td align="center">
            <p class="cc-muted" style="margin:0 0 6px 0;font-family:{_FUENTE};font-size:13px;line-height:20px;color:#6b6e76;">{_e(texto_pie)}</p>
            <p class="cc-muted" style="margin:0;font-family:{_FUENTE};font-size:13px;line-height:20px;color:#6b6e76;">{MARCA} · {LEMA}</p>
          </td></tr>
        </table>
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""
