"""Vista previa de los correos de ColdCare: genera un HTML por cada aviso con datos de ejemplo.

Uso:
    python -m scripts.vista_previa_correos                       # escribe vista_previa_correos/*.html
    python -m scripts.vista_previa_correos --salida /ruta        # otra carpeta
    python -m scripts.vista_previa_correos --enviar a@x.com,b@y.com   # los ENVÍA de verdad (con el .env)

Abre los .html en el navegador (modo oscuro: cambia el tema del sistema). `--enviar` usa los mismos
transportes que el backend; necesita EMAIL_ENABLED y las credenciales SMTP en el entorno.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from src.application.use_cases.generar_alerta import AvisoExcursion
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.config import get_settings
from src.infrastructure.notifications.correos_cuenta import correo_cuenta_creada, correo_recuperar_password
from src.infrastructure.notifications.notificacion_service import (
    componer_correo_excursion,
    componer_correo_preventivo,
)
from src.infrastructure.notifications.transporte_email import MensajeCorreo, construir_transporte

DISPOSITIVO = {"nombre": "Refrigerador de vacunas", "ubicacion": "Botica Central · Almacén"}


def ejemplos(frontend_url: str, remitente: str, destinatario: str) -> dict[str, MensajeCorreo]:
    """Un MensajeCorreo por tipo de aviso, con datos de ejemplo (no tocan la base de datos)."""
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    base = AvisoExcursion(
        alerta_id=uuid4(), device_id="ESP32_FARMACIA_01", temperatura=11.4, captured_at=ahora,
        received_at=ahora, time_quality="synced",
    )
    cuenta = correo_cuenta_creada("Brenda Gamio", destinatario, frontend_url)
    clave = correo_recuperar_password(
        "Brenda Gamio", f"{frontend_url or 'https://coldcare.example'}/restablecer-password#token=EJEMPLO", 30
    )
    def _m(asunto: str, cuerpo: str, html: str) -> MensajeCorreo:
        return MensajeCorreo(remitente, destinatario, asunto, cuerpo, html=html)

    return {
        "01_cuenta_creada": _m(cuenta.asunto, cuenta.cuerpo, cuenta.html),
        "02_recuperar_password": _m(clave.asunto, clave.cuerpo, clave.html),
        "03_riesgo_preventivo": componer_correo_preventivo(
            replace(base, temperatura=7.6, nivel=NivelRiesgo.RIESGO_PREVENTIVO),
            remitente, destinatario, frontend_url, DISPOSITIVO,
        ),
        "04_excursion_critica": componer_correo_excursion(base, remitente, destinatario, frontend_url, DISPOSITIVO),
        "05_excursion_diferida": componer_correo_excursion(
            replace(base, temperatura=1.2, captured_at=ahora - timedelta(hours=3)),
            remitente, destinatario, frontend_url, DISPOSITIVO,
        ),
    }


async def enviar(mensajes: dict[str, MensajeCorreo]) -> int:
    transporte = construir_transporte(get_settings())
    if transporte is None:
        print("No hay transporte de correo: define EMAIL_ENABLED=true y las credenciales SMTP.")
        return 1
    fallos = 0
    for nombre, mensaje in mensajes.items():
        try:
            await transporte.enviar(mensaje)
            print(f"  ✓ {nombre}")
        except Exception as exc:
            fallos += 1
            print(f"  ✗ {nombre}: {type(exc).__name__}")
    return 1 if fallos else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--salida", default="vista_previa_correos")
    parser.add_argument("--enviar", help="Correos separados por coma: los envía de verdad")
    args = parser.parse_args(argv)
    ajustes = get_settings()
    destinatario = args.enviar or "destino@ejemplo.com"
    mensajes = ejemplos(ajustes.frontend_url or "https://coldcare.example", ajustes.correo_remitente or "coldcare@ejemplo.com", destinatario)
    if args.enviar:
        return asyncio.run(enviar(mensajes))
    carpeta = Path(args.salida)
    carpeta.mkdir(parents=True, exist_ok=True)
    for nombre, mensaje in mensajes.items():
        (carpeta / f"{nombre}.html").write_text(mensaje.html or "", encoding="utf-8")
        print(f"{carpeta / (nombre + '.html')}  ←  {mensaje.asunto}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
