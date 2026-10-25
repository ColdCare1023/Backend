"""Da de alta un dispositivo en el backend local y emite su credencial MQTT.

Uso:  python scripts/crear_dispositivo.py ESP32_FARMACIA_01 [--aceptar-privacidad]

El token se guarda en secrets/<device_id>.token (ignorado por git) y nunca se
imprime. Volver a ejecutarlo rota la credencial (HU-44): el backend invalida la
anterior y expulsa la sesión MQTT que la usaba (BE-002).

BE-014: en una instalación nueva el administrador aún no aceptó la política
de privacidad y el backend responde 403. El consentimiento no se presume: se
acepta solo si quien ejecuta el script lo pide con --aceptar-privacidad (o
iniciando sesión una vez en el frontend).
"""
import sys

import httpx
from aprovisionar import API, aprovisionar, cabecera_admin


def main() -> None:
    argumentos = [a for a in sys.argv[1:] if not a.startswith("--")]
    aceptar_privacidad = "--aceptar-privacidad" in sys.argv[1:]
    if len(argumentos) != 1:
        raise SystemExit(__doc__)
    dev = argumentos[0]
    with httpx.Client(timeout=15) as http:
        cabecera = cabecera_admin(http)
        try:
            aprovisionar(http, cabecera, dev, "aprovisionamiento local")
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 403 or "privacidad" not in error.response.text:
                raise
            if not aceptar_privacidad:
                raise SystemExit(
                    "El administrador no ha aceptado la política de privacidad (BE-014). Inicie sesión "
                    "una vez en el frontend, o vuelva a ejecutar con --aceptar-privacidad si usted es "
                    "esa persona y la acepta."
                ) from None
            http.post(f"{API}/api/auth/privacidad/aceptar", headers=cabecera).raise_for_status()
            aprovisionar(http, cabecera, dev, "aprovisionamiento local")
    print(f"{dev}: credencial emitida por el backend (token en secrets/{dev}.token)")


main()
