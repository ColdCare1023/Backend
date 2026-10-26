"""Aprovisionamiento de dispositivos contra el backend local (BE-002).

La credencial MQTT de un dispositivo la emite el backend
(POST /api/dispositivos/{id}/credenciales/rotar) y la aprovisiona en la
autenticación integrada de EMQX con la API de administración del broker
(usuario = device_id, contraseña = token). Ningún script crea usuarios de
dispositivo directamente en EMQX: eso dejaría una credencial que el backend
no puede revocar.

Credenciales del administrador: COLDCARE_ADMIN_EMAIL / COLDCARE_ADMIN_PASSWORD
(si no están, las del usuario semilla "Admin Demo" de scripts/seed_dev.py, cuya
contraseña está en la variable SEED_ADMIN_PASSWORD de infra-local/.env).
"""
import os
import re
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent.parent
API = os.environ.get("COLDCARE_API", "http://localhost:8000")


def _env_local() -> dict[str, str]:
    archivo = RAIZ / ".env"
    if not archivo.exists():
        return {}
    lineas = archivo.read_text(encoding="utf-8").splitlines()
    return dict(linea.split("=", 1) for linea in lineas if "=" in linea and not linea.startswith("#"))


def credenciales_semilla(nombre: str) -> tuple[str, str]:
    """BE-023: email del usuario semilla `nombre` (scripts/seed_dev.py) y su
    contraseña, tomada de la variable SEED_* que init.sh genera en .env."""
    seed = (RAIZ.parent / "scripts/seed_dev.py").read_text(encoding="utf-8")
    m = re.search(rf'\("{re.escape(nombre)}", "([^"]+)", "([A-Z_]+)"', seed)
    if m is None:
        raise SystemExit(f"No se encontró el usuario semilla {nombre!r} en scripts/seed_dev.py.")
    email, variable = m.groups()
    clave = os.environ.get(variable) or _env_local().get(variable)
    if not clave:
        raise SystemExit(f"Defina {variable} (infra-local/.env, ver init.sh) para el usuario {email}.")
    return email, clave


def credenciales_admin() -> tuple[str, str]:
    email, clave = os.environ.get("COLDCARE_ADMIN_EMAIL"), os.environ.get("COLDCARE_ADMIN_PASSWORD")
    if email and clave:
        return email, clave
    return credenciales_semilla("Admin Demo")


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
