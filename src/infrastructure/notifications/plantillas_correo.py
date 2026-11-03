"""Plantilla HTML de los correos de ColdCare.

Un solo diseño para todos los avisos (cuenta creada, contraseña, alertas): estilo sobrio de GitHub/AWS: marca pequeña
sobre fondo blanco, banner de estado, titular, lectura térmica con su escala, tabla de datos con
borde fino, botón de acción y pie discreto. Está hecha para clientes de correo, no para navegadores:

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
    if not filas:
        return ""
    celdas = []
    for i, (etiqueta, valor) in enumerate(filas):
        borde = "" if i == len(filas) - 1 else "border-bottom:1px solid #d1d9e0;"
        celdas.append(
            f'<tr><td class="cc-muted cc-linea" style="padding:10px 16px;{borde}font-family:{_FUENTE};font-size:13px;'
            f'color:#59636e;width:38%;vertical-align:top;">{_e(etiqueta)}</td>'
            f'<td class="cc-texto cc-linea" style="padding:10px 16px;{borde}font-family:{_FUENTE};font-size:13px;'
            f'color:#1f2328;font-weight:600;vertical-align:top;">{_e(valor)}</td></tr>'
        )
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" class="cc-linea" '
        'style="margin:20px 0 0 0;border:1px solid #d1d9e0;border-radius:6px;border-collapse:separate;">'
        f'{"".join(celdas)}</table>'
    )


def _boton(boton: Boton, color: str) -> str:
    return (
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:24px 0 0 0;">'
        f'<tr><td align="center" bgcolor="{color}" style="border-radius:6px;background-color:{color};">'
        f'<a href="{_url_segura(boton.url)}" target="_blank" style="display:inline-block;padding:10px 20px;'
        f"font-family:{_FUENTE};font-size:14px;font-weight:600;color:#ffffff;text-decoration:none;"
        f'border-radius:6px;">{_e(boton.texto)}</a></td></tr></table>'
    )


def _marca() -> str:
    """Logotipo hecho con una celda (sin imagen): cuadrado azul hielo con la «C» y el nombre."""
    return (
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr>'
        f'<td align="center" width="28" height="28" style="width:28px;height:28px;background-color:{_HIELO};'
        f"border-radius:7px;font-family:{_FUENTE};font-size:16px;font-weight:800;color:#06202f;\">C</td>"
        f'<td class="cc-texto" style="padding-left:10px;font-family:{_FUENTE};font-size:16px;font-weight:600;color:#1f2328;">{MARCA}</td>'
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
        f'<p class="cc-texto" style="margin:12px 0 0 0;font-family:{_FUENTE};font-size:14px;line-height:22px;'
        f'color:#1f2328;">{_e(p)}</p>'
        for p in parrafos
    )
    bloque_escala = (
        f'<div class="cc-linea" style="margin-top:20px;padding:16px;border:1px solid #d1d9e0;border-radius:6px;">{escala}</div>'
        if escala
        else ""
    )
    bloque_nota = (
        f'<p class="cc-muted" style="margin:24px 0 0 0;font-family:{_FUENTE};font-size:13px;line-height:20px;color:#59636e;">{_e(nota)}</p>'
        if nota
        else ""
    )
    texto_pie = pie or "Recibes este mensaje porque eres parte del equipo de ColdCare. Es un aviso automático: no respondas a este correo."
    banner = (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
        f'<td class="cc-banner cc-b-{tono}" style="padding:10px 14px;background-color:{suave};border:1px solid {acento};'
        f'border-radius:6px;font-family:{_FUENTE};font-size:13px;font-weight:600;color:{acento};">'
        f'<span style="font-size:15px;">&#9679;</span>&nbsp; {_e(etiqueta)}</td></tr></table>'
    )
    return f"""<!DOCTYPE html>
<html lang="es" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{_e(asunto)}</title>
<style>
  @media only screen and (max-width:620px) {{ .cc-tarjeta {{ width:100% !important; }} .cc-pad {{ padding:20px 16px !important; }} }}
  @media (prefers-color-scheme: dark) {{
    .cc-fondo {{ background-color:#0d1117 !important; }}
    .cc-tarjeta {{ background-color:#0d1117 !important; }}
    .cc-texto {{ color:#e6edf3 !important; }}
    .cc-muted {{ color:#9198a1 !important; }}
    .cc-linea {{ border-color:#30363d !important; }}
    .cc-banner {{ background-color:#161b22 !important; }}
    .cc-t-critico, .cc-b-critico {{ color:#ff7b72 !important; }}
    .cc-t-aviso, .cc-b-aviso {{ color:#f0a24a !important; }}
    .cc-t-ok, .cc-b-ok {{ color:#3fb950 !important; }}
    .cc-t-info, .cc-b-info {{ color:#79c0ff !important; }}
  }}
</style>
</head>
<body class="cc-fondo" style="margin:0;padding:0;background-color:#ffffff;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;font-size:1px;line-height:1px;color:#ffffff;">{_e(preheader)}&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" class="cc-fondo" style="background-color:#ffffff;">
  <tr><td align="center" style="padding:24px 12px;">
    <table role="presentation" width="560" cellspacing="0" cellpadding="0" border="0" class="cc-tarjeta" style="width:560px;max-width:560px;background-color:#ffffff;">
      <tr><td class="cc-linea" style="padding:0 0 18px 0;border-bottom:1px solid #d1d9e0;">{_marca()}</td></tr>
      <tr><td class="cc-pad" style="padding:24px 0 8px 0;">
        {banner}
        <h1 class="cc-texto" style="margin:22px 0 0 0;font-family:{_FUENTE};font-size:22px;line-height:30px;font-weight:600;color:#1f2328;">{_e(titulo)}</h1>
        {cuerpo_parrafos}
        {bloque_escala}
        {_detalles(detalles or [])}
        {_boton(boton, acento) if boton else ""}
        {bloque_nota}
      </td></tr>
      <tr><td class="cc-linea" style="padding:20px 0 0 0;border-top:1px solid #d1d9e0;">
        <p class="cc-muted" style="margin:0;font-family:{_FUENTE};font-size:12px;line-height:18px;color:#59636e;">{_e(texto_pie)}</p>
        <p class="cc-muted" style="margin:8px 0 0 0;font-family:{_FUENTE};font-size:12px;line-height:18px;color:#59636e;">{MARCA} · {LEMA}</p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""
