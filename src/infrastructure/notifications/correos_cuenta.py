"""Correos de cuenta: bienvenida, contraseña olvidada y códigos de verificación (texto plano + HTML con la plantilla de ColdCare)."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from src.infrastructure.notifications.plantillas_correo import Boton, renderizar, url_logo


@dataclass(frozen=True, slots=True)
class CorreoComposto:
    asunto: str
    cuerpo: str
    html: str


def correo_cuenta_creada(
    nombre: str, email: str, frontend_url: str = "", *, logo_url: str | None = None
) -> CorreoComposto:
    """Se envía al crear la cuenta. Nunca lleva la contraseña: la entrega el administrador.

    Sin filas tipo «Usuario: … / Contraseña: …» (parecen credenciales y Gmail las trata como spam):
    el correo de acceso va dentro de una frase."""
    enlace = frontend_url.rstrip("/") if frontend_url else ""
    asunto = "Tu cuenta de ColdCare está lista"
    lineas = [
        f"Hola {nombre},",
        "",
        "Se creó tu cuenta en ColdCare, el sistema de monitoreo de la cadena de frío.",
        f"Entrarás con este mismo correo ({email}).",
        "La contraseña temporal te la entrega el administrador; el sistema te pedirá cambiarla en tu primer acceso.",
    ]
    if enlace:
        lineas += ["", f"Ingresa aquí: {enlace}"]
    lineas += ["", "Si no esperabas este mensaje, avisa al administrador."]
    html = renderizar(
        asunto=asunto,
        logo_url=logo_url or url_logo(enlace),
        preheader="Ya puedes ingresar a ColdCare con tu correo. Tu contraseña temporal te la da el administrador.",
        tono="ok",
        etiqueta="Cuenta creada",
        titulo=f"Bienvenido/a, {nombre}",
        parrafos=[
            "Se creó tu cuenta en ColdCare, el sistema que vigila la temperatura de tus refrigeradores "
            "y te avisa antes de que un lote se pierda.",
            f"Entrarás con este mismo correo ({email}). Tu contraseña es temporal, te la entrega el "
            "administrador. En tu primer acceso el sistema te pedirá elegir una propia.",
        ],
        boton=Boton("Ingresar a ColdCare", enlace) if enlace else None,
        nota="¿No esperabas este mensaje? Avisa al administrador: alguien pudo escribir tu correo por error.",
    )
    return CorreoComposto(asunto, "\n".join(lineas), html)


def correo_recuperar_password(nombre: str, enlace: str, minutos: int, *, logo_url: str | None = None) -> CorreoComposto:
    """«Olvidé mi contraseña»: enlace de un solo uso con vida corta."""
    asunto = "Restablece tu contraseña de ColdCare"
    cuerpo = (
        f"Hola {nombre}:\n\n"
        "Recibimos una solicitud para restablecer la contraseña de tu cuenta.\n"
        f"Usa este enlace en los próximos {minutos} minutos (solo funciona una vez):\n\n"
        f"{enlace}\n\n"
        "Si no lo solicitaste, ignora este mensaje: tu contraseña no cambia."
    )
    html = renderizar(
        asunto=asunto,
        logo_url=logo_url or url_logo(enlace),
        preheader=f"Tu enlace para elegir una contraseña nueva vence en {minutos} minutos.",
        tono="info",
        etiqueta="Seguridad de la cuenta",
        titulo="Restablece tu contraseña",
        parrafos=[
            f"Hola {nombre}, recibimos una solicitud para restablecer la contraseña de tu cuenta de ColdCare. "
            "Pulsa el botón para elegir una nueva.",
        ],
        detalles=[
            ("Vigencia del enlace", f"{minutos} minutos"),
            ("Uso", "Funciona una sola vez"),
        ],
        boton=Boton("Elegir una contraseña nueva", enlace),
        nota="¿No lo solicitaste? Ignora este mensaje: tu contraseña no cambia. Si te preocupa tu cuenta, avisa al administrador.",
    )
    return CorreoComposto(asunto, cuerpo, html)


# ── Códigos de verificación de un solo uso ────────────────────────────────
#
# El código nunca va en el asunto ni en el preheader: ambos se ven en la
# notificación del teléfono bloqueado.


def _vigencia(minutos: int) -> str:
    if minutos % 60 == 0 and minutos >= 120:
        return f"{minutos // 60} horas"
    if minutos == 60:
        return "1 hora"
    return f"{minutos} minutos"


def enlace_codigo(url_codigo: str, email: str, modo: str) -> str:
    """Página del frontend donde se ingresa el código, con el correo ya escrito."""
    return f"{url_codigo}?{urlencode({'email': email, 'modo': modo})}"


def correo_codigo_login(nombre: str, codigo: str, minutos: int, *, logo_url: str | None = None) -> CorreoComposto:
    """Segundo factor del inicio de sesión con contraseña. `logo_url`: ver `plantillas_correo.url_logo`."""
    asunto = "Tu código de acceso a ColdCare"
    vigencia = _vigencia(minutos)
    cuerpo = (
        f"Hola {nombre}:\n\n"
        "Alguien ingresó tu correo y contraseña en ColdCare. Para terminar de iniciar sesión, "
        "escribe este código en la pantalla de verificación:\n\n"
        f"    {codigo}\n\n"
        f"Vence en {vigencia} y solo funciona una vez.\n\n"
        "Si no fuiste tú, alguien conoce tu contraseña: no compartas este código, cámbiala cuanto antes "
        "y avisa al administrador."
    )
    html = renderizar(
        asunto=asunto,
        logo_url=logo_url,
        preheader=f"Tu código para entrar a ColdCare vence en {vigencia}.",
        tono="marca",
        etiqueta="Verificación de acceso",
        titulo="Confirma que eres tú",
        parrafos=[
            f"Hola {nombre}, para terminar de iniciar sesión en ColdCare escribe este código en la pantalla "
            "de verificación.",
        ],
        codigo=codigo,
        codigo_vigencia=f"Vence en {vigencia} · funciona una sola vez",
        nota="¿No fuiste tú? Alguien conoce tu contraseña. No compartas este código con nadie (ColdCare nunca "
        "te lo pedirá), cambia tu contraseña y avisa al administrador.",
    )
    return CorreoComposto(asunto, cuerpo, html)


def correo_codigo_recuperacion(
    nombre: str,
    codigo: str,
    minutos: int,
    enlace_pagina: str,
    enlace_legado: str | None = None,
    minutos_enlace: int | None = None,
    *,
    logo_url: str | None = None,
) -> CorreoComposto:
    """«Olvidé mi contraseña»: código de un solo uso para elegir una contraseña
    nueva. El botón del HTML lleva a la pantalla donde se escribe el código
    (como en la activación). Si se pasa `enlace_legado`, el enlace de un solo uso
    de la versión anterior (sigue funcionando) va solo en el texto plano: en el
    HTML el código es la única vigencia que se lee primero."""
    asunto = "Restablece tu contraseña de ColdCare"
    vigencia = _vigencia(minutos)
    lineas = [
        f"Hola {nombre}:",
        "",
        "Recibimos una solicitud para restablecer la contraseña de tu cuenta de ColdCare.",
        "Escribe este código en la pantalla de recuperación:",
        "",
        f"    {codigo}",
        "",
        f"Vence en {vigencia} y solo funciona una vez. Pantalla de recuperación: {enlace_pagina}",
    ]
    if enlace_legado:
        lineas += [
            "",
            f"También puedes usar este enlace en los próximos {_vigencia(minutos_enlace or minutos)} (solo funciona una vez):",
            "",
            enlace_legado,
        ]
    lineas += ["", "Si no lo solicitaste, ignora este mensaje: tu contraseña no cambia."]
    html = renderizar(
        asunto=asunto,
        logo_url=logo_url or url_logo(enlace_pagina),
        preheader=f"Tu código para elegir una contraseña nueva vence en {vigencia}.",
        tono="marca",
        etiqueta="Seguridad de la cuenta",
        titulo="Restablece tu contraseña",
        parrafos=[
            f"Hola {nombre}, recibimos una solicitud para restablecer la contraseña de tu cuenta de ColdCare. "
            "Escribe este código en la pantalla de recuperación para elegir una nueva.",
        ],
        codigo=codigo,
        codigo_vigencia=f"Vence en {vigencia} · funciona una sola vez",
        boton=Boton("Escribir el código", enlace_pagina),
        nota="¿No lo solicitaste? Ignora este mensaje: tu contraseña no cambia y nadie puede usar el código sin "
        "acceso a tu correo. Si te preocupa tu cuenta, avisa al administrador.",
    )
    return CorreoComposto(asunto, "\n".join(lineas), html)


def correo_activacion_cuenta(
    nombre: str,
    email: str,
    codigo: str,
    horas: int,
    enlace_activacion: str,
    frontend_url: str = "",
    *,
    logo_url: str | None = None,
) -> CorreoComposto:
    """Bienvenida de una cuenta creada por un administrador: explica cómo
    elegir la contraseña con el código de activación. Nunca lleva una contraseña ni filas
    tipo «Usuario: …»: el correo de acceso va dentro de una frase."""
    asunto = "Activa tu cuenta de ColdCare"
    vigencia = _vigencia(horas * 60)
    inicio = frontend_url.rstrip("/") if frontend_url else ""
    lineas = [
        f"Hola {nombre},",
        "",
        "Un administrador creó tu cuenta en ColdCare, el sistema de monitoreo de la cadena de frío.",
        f"Entrarás con este mismo correo ({email}). Tú eliges tu contraseña al activar la cuenta:",
        "",
        f"  1. Abre {enlace_activacion}",
        "  2. Escribe este código de activación:",
        "",
        f"    {codigo}",
        "",
        "  3. Elige una contraseña de al menos 10 caracteres que combine letras y números.",
        "",
        f"El código vence en {vigencia} y solo funciona una vez. Si vence, pide uno nuevo en la misma pantalla "
        "con «¿Olvidaste tu contraseña?».",
    ]
    if inicio:
        lineas += ["", f"Después ingresa aquí: {inicio}"]
    lineas += ["", "Si no esperabas este mensaje, avisa al administrador."]
    html = renderizar(
        asunto=asunto,
        logo_url=logo_url or url_logo(inicio or enlace_activacion),
        preheader=f"Un administrador te dio acceso a ColdCare. Tú eliges tu contraseña; el código vence en {vigencia}.",
        tono="marca",
        etiqueta="Activa tu cuenta",
        titulo=f"Bienvenido/a, {nombre}",
        parrafos=[
            "Un administrador creó tu cuenta en ColdCare, el sistema que vigila la temperatura de tus "
            "refrigeradores y te avisa antes de que un lote se pierda.",
            f"Entrarás con este mismo correo ({email}). Tú eliges tu contraseña al activar la cuenta: "
            "abre la pantalla de activación, escribe este código y elige una de al menos 10 caracteres, "
            "con letras y números.",
        ],
        codigo=codigo,
        codigo_vigencia=f"Vence en {vigencia} · funciona una sola vez",
        boton=Boton("Activar mi cuenta", enlace_activacion),
        nota="¿No esperabas este mensaje? Avisa al administrador: alguien pudo escribir tu correo por error. "
        "No compartas este código: ColdCare nunca te lo pedirá.",
    )
    return CorreoComposto(asunto, "\n".join(lineas), html)
