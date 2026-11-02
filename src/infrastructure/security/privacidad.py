"""Minimización de datos personales en bitácoras (Ley N.° 29733).

Compartido por la auditoría de login (HU-39), de usuarios (HU-41) y de avisos
por correo (HU-53): ningún correo completo entra en `audit_logs`, que es
inmutable por diseño.
"""


def enmascarar_telefono(telefono: str | None) -> str | None:
    """+51999888777 -> +51•••••777: lo justo para reconocerlo, nunca el número completo."""
    if not telefono:
        return None
    return f"{telefono[:3]}{'•' * max(len(telefono) - 6, 0)}{telefono[-3:]}"


def enmascarar_email(email: str) -> str:
    """Reduce un correo a `p***@dominio` para la bitácora.

    Ley N.° 29733, principio de proporcionalidad: el correo tecleado en un
    intento FALLIDO puede no pertenecer a ningún usuario del sistema —un error
    de escritura, o la dirección de un tercero usada por quien ataca—, y
    `audit_logs` es inmutable por diseño, así que no hay vía de rectificación
    ni de supresión posterior.

    Se conserva lo que da valor forense a RF-16: la inicial y el dominio bastan
    para distinguir un ataque dirigido a una cuenta concreta de un barrido, y
    para correlacionar intentos (el mismo correo produce siempre la misma
    máscara). Lo que se descarta es la identificación directa del titular.
    """
    if not email or "@" not in email:
        # Sin forma de correo no hay dominio que preservar; se guarda solo la
        # longitud, que sigue permitiendo distinguir intentos entre sí.
        return f"<sin formato de correo, {len(email)} caracteres>"
    local, _, dominio = email.partition("@")
    inicial = local[0] if local else ""
    return f"{inicial}***@{dominio}"
