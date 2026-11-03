"""Correos de cuenta: bienvenida y contraseña olvidada (texto plano + HTML con la plantilla de ColdCare)."""

from __future__ import annotations

from dataclasses import dataclass

from src.infrastructure.notifications.plantillas_correo import Boton, renderizar


@dataclass(frozen=True, slots=True)
class CorreoComposto:
    asunto: str
    cuerpo: str
    html: str


def correo_cuenta_creada(nombre: str, email: str, frontend_url: str = "") -> CorreoComposto:
    """Se envía al crear la cuenta. Nunca lleva la contraseña: la entrega el administrador."""
    enlace = frontend_url.rstrip("/") if frontend_url else ""
    asunto = "[ColdCare] Tu cuenta fue creada"
    lineas = [
        f"Hola {nombre},",
        "",
        "Se creó tu cuenta en ColdCare, el sistema de monitoreo de la cadena de frío.",
        f"Tu usuario es este correo: {email}.",
        "La contraseña temporal te la entrega el administrador; el sistema te pedirá cambiarla en tu primer acceso.",
    ]
    if enlace:
        lineas += ["", f"Ingresa aquí: {enlace}"]
    lineas += ["", "Si no esperabas este mensaje, avisa al administrador."]
    html = renderizar(
        asunto=asunto,
        preheader="Ya puedes ingresar a ColdCare con tu correo. Tu contraseña temporal te la da el administrador.",
        tono="ok",
        etiqueta="Cuenta creada",
        titulo=f"Bienvenido/a, {nombre}",
        parrafos=[
            "Se creó tu cuenta en ColdCare, el sistema que vigila la temperatura de tus refrigeradores "
            "y te avisa antes de que un lote se pierda.",
        ],
        detalles=[
            ("Usuario", email),
            ("Contraseña", "Temporal, te la entrega el administrador"),
            ("Primer acceso", "El sistema te pedirá elegir una contraseña propia"),
        ],
        boton=Boton("Ingresar a ColdCare", enlace) if enlace else None,
        nota="¿No esperabas este mensaje? Avisa al administrador: alguien pudo escribir tu correo por error.",
    )
    return CorreoComposto(asunto, "\n".join(lineas), html)


def correo_recuperar_password(nombre: str, enlace: str, minutos: int) -> CorreoComposto:
    """«Olvidé mi contraseña»: enlace de un solo uso con vida corta."""
    asunto = "[ColdCare] Restablecer contraseña"
    cuerpo = (
        f"Hola {nombre}:\n\n"
        "Recibimos una solicitud para restablecer la contraseña de tu cuenta.\n"
        f"Usa este enlace en los próximos {minutos} minutos (solo funciona una vez):\n\n"
        f"{enlace}\n\n"
        "Si no lo solicitaste, ignora este mensaje: tu contraseña no cambia."
    )
    html = renderizar(
        asunto=asunto,
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
