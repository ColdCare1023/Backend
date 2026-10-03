"""Da de alta (o rota el token de) un dispositivo en el EMQX local.

Uso:  python scripts/crear_dispositivo.py ESP32_FARMACIA_01 [--rotar]

El token se genera aleatorio y se guarda en secrets/<device_id>.token (ignorado
por git). Con --rotar se genera uno nuevo aunque ya exista (HU-44).
"""
import secrets
import sys
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent.parent
ENV = dict(linea.split("=", 1) for linea in (RAIZ / ".env").read_text().splitlines() if "=" in linea)
API = "http://localhost:18083/api/v5"
URL_USUARIOS = f"{API}/authentication/password_based:built_in_database/users"


def main() -> None:
    dev = sys.argv[1]
    archivo = RAIZ / "secrets" / f"{dev}.token"
    if "--rotar" in sys.argv or not archivo.exists():
        archivo.write_text(secrets.token_hex(16))
    token = archivo.read_text()

    r = httpx.post(f"{API}/login", json={"username": ENV["EMQX_DASHBOARD_USER"],
                                         "password": ENV["EMQX_DASHBOARD_PASSWORD"]})
    r.raise_for_status()
    cab = {"Authorization": f"Bearer {r.json()['token']}"}
    r = httpx.post(URL_USUARIOS, headers=cab, json={"user_id": dev, "password": token})
    if r.status_code == 409:
        r = httpx.put(f"{URL_USUARIOS}/{dev}", headers=cab, json={"password": token})
    r.raise_for_status()
    print(f"{dev}: usuario MQTT listo (token en secrets/{dev}.token)")


main()
