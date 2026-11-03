"""Plantilla HTML de los correos de ColdCare.

Un solo diseño para todos los avisos (cuenta creada, contraseña, alertas): barra de marca,
etiqueta de estado con color, titular, bloque de datos, escala térmica opcional, botón de acción y
nota de seguridad. Está hecha para clientes de correo, no para navegadores:

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

# Tono → (color de acento, fondo suave de la etiqueta)
TONOS: dict[str, tuple[str, str]] = {
    "critico": ("#d92d20", "#fef3f2"),
    "aviso": ("#dc6803", "#fffaeb"),
    "ok": ("#039855", "#ecfdf3"),
    "info": ("#1570ef", "#eff8ff"),
}

MARCA = "ColdCare"
LEMA = "Monitoreo de cadena de frío"

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
    """Barra horizontal con la zona segura (2-8 °C) en verde y un marcador en la temperatura leída.

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
    celda = "padding:0;font-size:0;line-height:0;height:10px;"
    return f"""
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin:6px 0 4px 0;">
  <tr>
    <td style="padding:0;width:{marcador - 3}%;">&nbsp;</td>
    <td align="center" style="padding:0;width:6%;font-family:{_FUENTE};font-size:11px;line-height:12px;color:{color};">&#9660;</td>
    <td style="padding:0;">&nbsp;</td>
  </tr>
</table>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border-radius:6px;overflow:hidden;">
  <tr>
    <td width="{izquierda}%" style="{celda}background-color:#93c5fd;">&nbsp;</td>
    <td width="{zona}%" style="{celda}background-color:#12b76a;">&nbsp;</td>
    <td width="{derecha}%" style="{celda}background-color:#fdba74;">&nbsp;</td>
  </tr>
</table>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin-top:6px;">
  <tr>
    <td width="{izquierda}%" align="right" style="padding:0;font-family:{_FUENTE};font-size:11px;color:#667085;">{minimo:g} °C</td>
    <td width="{zona}%" align="center" style="padding:0;font-family:{_FUENTE};font-size:11px;color:#039855;font-weight:600;">rango seguro</td>
    <td width="{derecha}%" align="left" style="padding:0;font-family:{_FUENTE};font-size:11px;color:#667085;">{maximo:g} °C</td>
  </tr>
</table>"""


def _detalles(filas: list[tuple[str, str]]) -> str:
    if not filas:
        return ""
    celdas = []
    for i, (etiqueta, valor) in enumerate(filas):
        borde = "" if i == len(filas) - 1 else "border-bottom:1px solid #e4e7ec;"
        celdas.append(
            f'<tr><td class="cc-muted cc-linea" style="padding:11px 0;{borde}font-family:{_FUENTE};font-size:13px;'
            f'color:#667085;width:40%;vertical-align:top;">{_e(etiqueta)}</td>'
            f'<td class="cc-texto cc-linea" style="padding:11px 0;{borde}font-family:{_FUENTE};font-size:14px;'
            f'color:#101828;font-weight:600;vertical-align:top;">{_e(valor)}</td></tr>'
        )
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" class="cc-caja" '
        'style="margin:22px 0 0 0;background-color:#f9fafb;border:1px solid #e4e7ec;border-radius:12px;">'
        '<tr><td style="padding:6px 20px;"><table role="presentation" width="100%" cellspacing="0" '
        f'cellpadding="0" border="0">{"".join(celdas)}</table></td></tr></table>'
    )


def _boton(boton: Boton, color: str) -> str:
    return (
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:28px 0 0 0;">'
        f'<tr><td align="center" bgcolor="{color}" style="border-radius:10px;background-color:{color};">'
        f'<a href="{_url_segura(boton.url)}" target="_blank" style="display:inline-block;padding:14px 30px;'
        f"font-family:{_FUENTE};font-size:15px;font-weight:700;color:#ffffff;text-decoration:none;"
        f'border-radius:10px;">{_e(boton.texto)}</a></td></tr></table>'
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
        f'<p class="cc-texto" style="margin:14px 0 0 0;font-family:{_FUENTE};font-size:15px;line-height:24px;'
        f'color:#344054;">{_e(p)}</p>'
        for p in parrafos
    )
    bloque_escala = (
        f'<div class="cc-linea" style="margin-top:24px;padding:16px 20px 14px 20px;border:1px solid #e4e7ec;border-radius:12px;">{escala}</div>'
        if escala
        else ""
    )
    bloque_nota = (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin-top:26px;">'
        f'<tr><td class="cc-nota" style="padding:14px 16px;background-color:{suave};border-left:4px solid {acento};'
        f'border-radius:6px;font-family:{_FUENTE};font-size:13px;line-height:20px;color:#475467;">{_e(nota)}</td></tr></table>'
        if nota
        else ""
    )
    texto_pie = pie or "Recibes este mensaje porque eres parte del equipo de ColdCare. Es un aviso automático: no respondas a este correo."
    return f"""<!DOCTYPE html>
<html lang="es" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{_e(asunto)}</title>
<style>
  @media only screen and (max-width:620px) {{ .cc-tarjeta {{ width:100% !important; }} .cc-pad {{ padding:24px 20px !important; }} }}
  @media (prefers-color-scheme: dark) {{
    .cc-fondo {{ background-color:#0b1220 !important; }}
    .cc-tarjeta {{ background-color:#111a2c !important; }}
    .cc-caja {{ background-color:#16213a !important; border-color:#27334d !important; }}
    .cc-texto {{ color:#e5eaf3 !important; }}
    .cc-muted {{ color:#98a2b3 !important; }}
    .cc-linea {{ border-color:#27334d !important; }}
    .cc-nota {{ background-color:#1b2742 !important; color:#c3cde0 !important; }}
  }}
</style>
</head>
<body class="cc-fondo" style="margin:0;padding:0;background-color:#eef2f6;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;font-size:1px;line-height:1px;color:#eef2f6;">{_e(preheader)}&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" class="cc-fondo" style="background-color:#eef2f6;">
  <tr><td align="center" style="padding:28px 12px;">
    <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" class="cc-tarjeta" style="width:600px;max-width:600px;background-color:#ffffff;border-radius:16px;overflow:hidden;">
      <tr><td style="background-color:#0b1f33;padding:22px 32px;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>
          <td style="font-family:{_FUENTE};font-size:20px;font-weight:800;color:#ffffff;letter-spacing:.2px;">&#10052;&#65039; {MARCA}</td>
          <td align="right" style="font-family:{_FUENTE};font-size:12px;color:#9fb3c8;">{LEMA}</td>
        </tr></table>
      </td></tr>
      <tr><td style="background-color:{acento};height:5px;line-height:5px;font-size:0;">&nbsp;</td></tr>
      <tr><td class="cc-pad" style="padding:34px 36px 30px 36px;">
        <span style="display:inline-block;padding:6px 12px;background-color:{suave};border-radius:999px;font-family:{_FUENTE};font-size:12px;font-weight:800;letter-spacing:.6px;color:{acento};text-transform:uppercase;">{_e(etiqueta)}</span>
        <h1 class="cc-texto" style="margin:16px 0 0 0;font-family:{_FUENTE};font-size:26px;line-height:32px;font-weight:800;color:#101828;">{_e(titulo)}</h1>
        {cuerpo_parrafos}
        {bloque_escala}
        {_detalles(detalles or [])}
        {_boton(boton, acento) if boton else ""}
        {bloque_nota}
      </td></tr>
      <tr><td class="cc-caja" style="padding:20px 36px 26px 36px;background-color:#f9fafb;border-top:1px solid #e4e7ec;">
        <p class="cc-muted" style="margin:0;font-family:{_FUENTE};font-size:12px;line-height:19px;color:#667085;">{_e(texto_pie)}</p>
        <p class="cc-muted" style="margin:8px 0 0 0;font-family:{_FUENTE};font-size:12px;line-height:19px;color:#98a2b3;">{MARCA} · {LEMA}</p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""
