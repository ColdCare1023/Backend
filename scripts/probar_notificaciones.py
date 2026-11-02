"""Prueba REAL de los avisos de bienvenida: manda un correo y un SMS de verdad.

Usa exactamente el camino que sigue el backend al crear una cuenta (mismos transportes y
mismos textos), con la configuración del entorno (`.env`): no hay un canal de simulación.
Imprime qué pasó con cada canal y sale con 1 si alguno falló o no está configurado.

Uso:
    export PRUEBA_EMAIL=tu_correo@ejemplo.com
    export PRUEBA_TELEFONO=+51999888777          # E.164; opcional, sin él solo prueba el correo
    python -m scripts.probar_notificaciones

Configuración mínima en `.env` (las credenciales las pones tú, nunca en el código):
    Correo (Gmail, con «contraseña de aplicación»):
        EMAIL_ENABLED=true  EMAIL_TRANSPORTE=smtp
        SMTP_HOST=smtp.gmail.com  SMTP_PORT=465  SMTP_SEGURIDAD=ssl
        SMTP_USER=cuenta@gmail.com  SMTP_PASSWORD=<contraseña de aplicación>
        EMAIL_FROM=cuenta@gmail.com  EMAIL_DESTINO_RESPALDO=cuenta@gmail.com
        FRONTEND_URL=https://tu-frontend
    SMS (Twilio; una cuenta de prueba solo envía a números verificados):
        SMS_ENABLED=true  SMS_ACCOUNT_SID=AC...  SMS_AUTH_TOKEN=...  SMS_FROM=+1...
"""

from __future__ import annotations

import asyncio
import os
import re
import sys

from src.infrastructure.config import get_settings
from src.infrastructure.notifications.bienvenida import ENVIADO, NotificadorBienvenida
from src.infrastructure.security.privacidad import enmascarar_email, enmascarar_telefono

E164 = re.compile(r"^\+[1-9]\d{7,14}$")


async def probar(email: str, telefono: str | None) -> tuple[str, str]:
    resultado = await NotificadorBienvenida(get_settings()).notificar("Prueba de ColdCare", email, telefono)
    return resultado.correo, resultado.sms


def main() -> int:
    email = os.getenv("PRUEBA_EMAIL", "").strip()
    telefono = os.getenv("PRUEBA_TELEFONO", "").strip() or None
    if not email:
        sys.exit("Falta PRUEBA_EMAIL (tu correo real).")
    if telefono and not E164.match(telefono):
        sys.exit("PRUEBA_TELEFONO debe estar en formato E.164, p. ej. +51999888777.")
    correo, sms = asyncio.run(probar(email, telefono))
    print(f"Correo  → {enmascarar_email(email)}: {correo}")
    print(f"SMS     → {enmascarar_telefono(telefono) or '(sin número)'}: {sms}")
    correo_ok = correo == ENVIADO
    sms_ok = sms == ENVIADO or telefono is None
    print("Revisa tu bandeja y tu teléfono: «enviado» significa que el proveedor aceptó el mensaje.")
    return 0 if correo_ok and sms_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
