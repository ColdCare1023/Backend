"""Aprovisionamiento de dispositivos contra el backend local (BE-002).

La credencial MQTT de un dispositivo la emite el backend
(POST /api/dispositivos/{id}/credenciales/rotar) y el broker la valida a
través del webhook /api/dispositivos/autenticar. Ningún script crea usuarios
de dispositivo directamente en la base interna de EMQX: eso dejaría una
credencial que el backend no puede revocar.

Credenciales del administrador: COLDCARE_ADMIN_EMAIL / COLDCARE_ADMIN_PASSWORD
(si no están, se toman del usuario semilla "Admin Demo" de scripts/seed_dev.py).
"""
import os
import re
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent.parent
API = os.environ.get("COLDCARE_API", "http://localhost:8000")


def credenciales_admin() -> tuple[str, str]:
    email, clave = os.environ.get("COLDCARE_ADMIN_EMAIL"), os.environ.get("COLDCARE_ADMIN_PASSWORD")
    if email and clave:
        return email, clave
    seed = (RAIZ.parent / "scripts/seed_dev.py").read_text(encoding="utf-8")
    m = re.search(r'\("Admin Demo", "([^"]+)", "([^"]+)"', seed)
    if m is None:
        raise SystemExit("Defina COLDCARE_ADMIN_EMAIL y COLDCARE_ADMIN_PASSWORD.")
    return m.group(1), m.group(2)


def cabecera_admin(http: httpx.Client) -> dict[str, str]:
    email, clave = credenciales_admin()
    r = http.post(f"{API}/api/auth/login", data={"username": email, "password": clave})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def aprovisionar(http: httpx.Client, cabecera: dict[str, str], device_id: str, motivo: str) -> str:
    """Da de alta el dispositivo (si no existe), rota su credencial y guarda
    el token en secrets/<device_id>.token. Devuelve el token."""
    r = http.post(f"{API}/api/dispositivos", headers=cabecera, json={"device_id": device_id})
    if r.status_code not in (201, 409):
        r.raise_for_status()
    r = http.post(
        f"{API}/api/dispositivos/{device_id}/credenciales/rotar", headers=cabecera, json={"motivo": motivo}
    )
    r.raise_for_status()
    token = r.json()["token"]
    destino = RAIZ / "secrets" / f"{device_id}.token"
    destino.parent.mkdir(exist_ok=True)
    destino.write_text(token)
    return token
