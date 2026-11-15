"""Prueba REAL del aviso de bienvenida: manda un correo de verdad.

Usa exactamente el camino que sigue el backend al crear una cuenta (mismo transporte y
mismos textos), con la configuración del entorno (`.env`): no hay un canal de simulación.
Imprime qué pasó con cada destinatario y sale con 1 si alguno falló o no está configurado.
HU-53 (v3.3): el SMS se retiró del alcance; este script ya no lo prueba.

Uso:
    export PRUEBA_EMAIL=tu_correo@ejemplo.com             # o varios, separados por coma: a@x.com,b@y.com
    python -m scripts.probar_notificaciones

Configuración mínima en `.env` (las credenciales las pones tú, nunca en el código):
    Correo (Gmail, con «contraseña de aplicación»):
        EMAIL_ENABLED=true  EMAIL_TRANSPORTE=smtp
        SMTP_HOST=smtp.gmail.com  SMTP_PORT=465  SMTP_SEGURIDAD=ssl
        SMTP_USER=cuenta@gmail.com  SMTP_PASSWORD=<contraseña de aplicación>
        EMAIL_FROM=cuenta@gmail.com  EMAIL_DESTINO_RESPALDO=cuenta@gmail.com
        FRONTEND_URL=https://tu-frontend
"""

from __future__ import annotations

import asyncio
import os
import sys

from src.infrastructure.config import get_settings
from src.infrastructure.notifications.bienvenida import ENVIADO, NotificadorBienvenida
from src.infrastructure.security.privacidad import enmascarar_email


def separar_correos(texto: str) -> list[str]:
    """«a@x.com, b@y.com» -> ['a@x.com', 'b@y.com'] (sin vacíos ni repetidos, en orden)."""
    vistos: list[str] = []
    for parte in texto.split(","):
        correo = parte.strip()
        if correo and correo not in vistos:
            vistos.append(correo)
    return vistos


async def probar(email: str) -> str:
    resultado = await NotificadorBienvenida(get_settings()).notificar("Prueba de ColdCare", email)
    return resultado.correo


def main() -> int:
    correos = separar_correos(os.getenv("PRUEBA_EMAIL", ""))
    if not correos:
        sys.exit("Falta PRUEBA_EMAIL (tu correo real; varios, separados por coma).")
    correo_ok = True
    for email in correos:
        correo = asyncio.run(probar(email))
        print(f"Correo  → {enmascarar_email(email)}: {correo}")
        correo_ok = correo_ok and correo == ENVIADO
    print("Revisa tu bandeja: «enviado» significa que el proveedor aceptó el mensaje.")
    return 0 if correo_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
