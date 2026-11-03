"""Plantilla HTML de los correos de ColdCare.

Un solo diseño para todos los avisos (cuenta creada, contraseña, alertas): cabecera oscura con la
marca, icono de estado, titular grande, lectura térmica destacada con su escala, bloque de datos,
botón de acción y nota de seguridad. Está hecha para clientes de correo, no para navegadores:

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
    estado = "FUERA DE RANGO" if fuera else "DENTRO DE RANGO"
    clase = "cc-t-critico" if fuera else "cc-t-ok"
    celda = "padding:0;font-size:0;line-height:0;height:12px;"
    return f"""
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
  <tr>
    <td class="cc-muted" style="padding:0;font-family:{_FUENTE};font-size:11px;letter-spacing:1px;font-weight:700;color:#667085;">LECTURA ACTUAL</td>
    <td align="right" class="{clase}" style="padding:0;font-family:{_FUENTE};font-size:11px;letter-spacing:1px;font-weight:800;color:{color};">{estado}</td>
  </tr>
  <tr>
    <td colspan="2" class="{clase}" style="padding:6px 0 14px 0;font-family:{_FUENTE};font-size:46px;line-height:50px;font-weight:800;color:{color};letter-spacing:-1px;">{temperatura:.1f}<span style="font-size:22px;font-weight:700;letter-spacing:0;"> °C</span></td>
  </tr>
</table>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
  <tr>
    <td style="padding:0;width:{marcador - 3}%;">&nbsp;</td>
    <td align="center" class="{clase}" style="padding:0;width:6%;font-family:{_FUENTE};font-size:12px;line-height:12px;color:{color};">&#9660;</td>
    <td style="padding:0;">&nbsp;</td>
  </tr>
</table>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border-radius:8px;overflow:hidden;">
  <tr>
    <td width="{izquierda}%" style="{celda}background-color:#7dd3fc;">&nbsp;</td>
    <td width="{zona}%" style="{celda}background-color:#12b76a;">&nbsp;</td>
    <td width="{derecha}%" style="{celda}background-color:#fdba74;">&nbsp;</td>
  </tr>
</table>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin-top:8px;">
  <tr>
    <td width="{izquierda}%" align="right" style="padding:0;font-family:{_FUENTE};font-size:11px;color:#667085;" class="cc-muted">{minimo:g} °C</td>
    <td width="{zona}%" align="center" style="padding:0;font-family:{_FUENTE};font-size:11px;color:#0e9f6e;font-weight:700;">rango seguro</td>
    <td width="{derecha}%" align="left" style="padding:0;font-family:{_FUENTE};font-size:11px;color:#667085;" class="cc-muted">{maximo:g} °C</td>
  </tr>
</table>"""


def _detalles(filas: list[tuple[str, str]]) -> str:
    if not filas:
        return ""
    celdas = []
    for i, (etiqueta, valor) in enumerate(filas):
        borde = "" if i == len(filas) - 1 else "border-bottom:1px solid #eaecf0;"
        celdas.append(
            f'<tr><td class="cc-muted cc-linea" style="padding:13px 0;{borde}font-family:{_FUENTE};font-size:13px;'
            f'color:#667085;width:38%;vertical-align:top;">{_e(etiqueta)}</td>'
            f'<td class="cc-texto cc-linea" align="right" style="padding:13px 0;{borde}font-family:{_FUENTE};font-size:14px;'
            f'color:#101828;font-weight:600;vertical-align:top;">{_e(valor)}</td></tr>'
        )
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
        f'style="margin:24px 0 0 0;">{"".join(celdas)}</table>'
    )


def _boton(boton: Boton, color: str) -> str:
    return (
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:30px 0 0 0;">'
        f'<tr><td align="center" bgcolor="{color}" style="border-radius:999px;background-color:{color};">'
        f'<a href="{_url_segura(boton.url)}" target="_blank" style="display:inline-block;padding:15px 34px;'
        f"font-family:{_FUENTE};font-size:15px;font-weight:700;color:#ffffff;text-decoration:none;"
        f'border-radius:999px;">{_e(boton.texto)} &rarr;</a></td></tr></table>'
    )


def _marca() -> str:
    """Logotipo hecho con una celda (sin imagen): cuadrado azul hielo con la «C» y el nombre."""
    return (
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr>'
        f'<td align="center" width="34" height="34" style="width:34px;height:34px;background-color:{_HIELO};'
        f"border-radius:10px;font-family:{_FUENTE};font-size:19px;font-weight:800;color:#06202f;\">C</td>"
        f'<td style="padding-left:11px;font-family:{_FUENTE};font-size:19px;font-weight:800;color:#ffffff;'
        f'letter-spacing:.2px;">{MARCA}</td></tr></table>'
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
        f'<p class="cc-texto" style="margin:14px 0 0 0;font-family:{_FUENTE};font-size:15px;line-height:25px;'
        f'color:#475467;">{_e(p)}</p>'
        for p in parrafos
    )
    bloque_escala = (
        f'<div class="cc-caja" style="margin-top:26px;padding:20px 22px 16px 22px;background-color:#f8fafc;border:1px solid #eaecf0;border-radius:14px;">{escala}</div>'
        if escala
        else ""
    )
    bloque_nota = (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin-top:28px;">'
        f'<tr><td class="cc-nota" style="padding:14px 16px;background-color:{suave};border-left:4px solid {acento};'
        f'border-radius:8px;font-family:{_FUENTE};font-size:13px;line-height:20px;color:#475467;">{_e(nota)}</td></tr></table>'
        if nota
        else ""
    )
    texto_pie = pie or "Recibes este mensaje porque eres parte del equipo de ColdCare. Es un aviso automático: no respondas a este correo."
    icono = (
        f'<table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr>'
        f'<td align="center" width="56" height="56" class="cc-ico cc-t-{tono}" style="width:56px;height:56px;background-color:{suave};border-radius:28px;'
        f'font-family:{_FUENTE};font-size:28px;line-height:56px;font-weight:800;color:{acento};">{_SIMBOLO[tono]}</td>'
        f'<td class="cc-t-{tono}" style="padding-left:14px;font-family:{_FUENTE};font-size:12px;font-weight:800;letter-spacing:1.4px;'
        f'color:{acento};text-transform:uppercase;">{_e(etiqueta)}</td></tr></table>'
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
  @media only screen and (max-width:620px) {{ .cc-tarjeta {{ width:100% !important; }} .cc-pad {{ padding:26px 20px !important; }} .cc-cab {{ padding:18px 20px !important; }} }}
  @media (prefers-color-scheme: dark) {{
    .cc-fondo {{ background-color:#05080f !important; }}
    .cc-tarjeta {{ background-color:#0f1626 !important; }}
    .cc-caja {{ background-color:#151e33 !important; border-color:#27334d !important; }}
    .cc-texto {{ color:#e5eaf3 !important; }}
    .cc-muted {{ color:#98a2b3 !important; }}
    .cc-linea {{ border-color:#27334d !important; }}
    .cc-nota {{ background-color:#1b2742 !important; color:#c3cde0 !important; }}
    .cc-ico {{ background-color:#1b2742 !important; }}
    .cc-t-critico {{ color:#ff7a6e !important; }}
    .cc-t-aviso {{ color:#ffb14a !important; }}
    .cc-t-ok {{ color:#3ddc97 !important; }}
    .cc-t-info {{ color:#7aa7ff !important; }}
  }}
</style>
</head>
<body class="cc-fondo" style="margin:0;padding:0;background-color:#f1f3f6;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;font-size:1px;line-height:1px;color:#f1f3f6;">{_e(preheader)}&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" class="cc-fondo" style="background-color:#f1f3f6;">
  <tr><td align="center" style="padding:32px 12px;">
    <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" class="cc-tarjeta" style="width:600px;max-width:600px;background-color:#ffffff;border-radius:20px;overflow:hidden;">
      <tr><td class="cc-cab" style="background-color:#070b14;padding:22px 36px;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>
          <td>{_marca()}</td>
          <td align="right" style="font-family:{_FUENTE};font-size:12px;color:#8aa0b8;">{LEMA}</td>
        </tr></table>
      </td></tr>
      <tr><td style="background-color:{acento};height:4px;line-height:4px;font-size:0;">&nbsp;</td></tr>
      <tr><td class="cc-pad" style="padding:36px 40px 32px 40px;">
        {icono}
        <h1 class="cc-texto" style="margin:22px 0 0 0;font-family:{_FUENTE};font-size:28px;line-height:34px;font-weight:800;color:#101828;letter-spacing:-.4px;">{_e(titulo)}</h1>
        {cuerpo_parrafos}
        {bloque_escala}
        {_detalles(detalles or [])}
        {_boton(boton, acento) if boton else ""}
        {bloque_nota}
      </td></tr>
      <tr><td class="cc-caja" style="padding:22px 40px 28px 40px;background-color:#f8fafc;border-top:1px solid #eaecf0;">
        <p class="cc-muted" style="margin:0;font-family:{_FUENTE};font-size:12px;line-height:19px;color:#667085;">{_e(texto_pie)}</p>
        <p class="cc-muted" style="margin:10px 0 0 0;font-family:{_FUENTE};font-size:12px;line-height:19px;color:#98a2b3;">{MARCA} · {LEMA}</p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""
