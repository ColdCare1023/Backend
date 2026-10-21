"""Da de alta un dispositivo en el backend local y emite su credencial MQTT.

Uso:  python scripts/crear_dispositivo.py ESP32_FARMACIA_01

El token se guarda en secrets/<device_id>.token (ignorado por git) y nunca se
imprime. Volver a ejecutarlo rota la credencial (HU-44): el backend invalida la
anterior y expulsa la sesión MQTT que la usaba (BE-002).
"""
import sys

import httpx
from aprovisionar import aprovisionar, cabecera_admin


def main() -> None:
    dev = sys.argv[1]
    with httpx.Client(timeout=15) as http:
        aprovisionar(http, cabecera_admin(http), dev, "aprovisionamiento local")
    print(f"{dev}: credencial emitida por el backend (token en secrets/{dev}.token)")


main()
