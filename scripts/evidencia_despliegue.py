"""HU-55: evidencia del despliegue en Railway (Definición de Hecho).

Uso:

    BACKEND_URL=https://<servicio>.up.railway.app EVIDENCIA_TOKEN=<JWT de administrador> \
    python -m scripts.evidencia_despliegue --salida docs/evidencias/HU-55_despliegue.json

Comprueba y deja en un JSON fechado:
- que la URL es HTTPS y responde `/health` (liveness, sin base de datos);
- `/health/detalle` (requiere un JWT de ADMINISTRADOR): base de datos,
  revisión de Alembic, MQTT, despachador, vigilancia SIN_DATOS, con qué
  comando arrancó el proceso (`arranque`: `scripts.arrancar_api` y 1 worker,
  HU-12 esc. 4) y lo que el backend ve detrás del proxy (esquema https e IP
  pública del cliente, B-11);
- que `/docs` y `/openapi.json` NO están publicados en producción;
- sin red: que el Procfile, railpack.json, railway.json y el Dockerfile del
  repositorio arrancan con el mismo comando (`configuracion_repositorio`).

Seguro sin red ni credenciales: un fallo de conexión, un cuerpo que no es JSON
o la falta de token no lanzan una traza; quedan registrados en el JSON y la
salida es 1. El token se lee del entorno y nunca se escribe en la evidencia.
"""

import argparse
import ipaddress
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from scripts.arrancar_api import COMANDO, LANZADOR, WORKERS, comandos_de_arranque

TIMEOUT_SEGUNDOS = 15


def _cuerpo_json(respuesta: httpx.Response) -> dict | None:
    if not respuesta.is_success:
        return None
    try:
        cuerpo = respuesta.json()
    except ValueError:
        return None
    return cuerpo if isinstance(cuerpo, dict) else None


def _ip_publica(valor: object) -> bool:
    """La IP que ve el backend es la del navegador (pública), no la del proxy
    interno de la plataforma (privada, CGNAT o loopback)."""
    try:
        return ipaddress.ip_address(str(valor)).is_global
    except ValueError:
        return False


def _consultar(cliente: httpx.Client, base: str, token: str | None) -> dict:
    evidencia: dict = {}
    salud = cliente.get(f"{base}/health")
    evidencia["health"] = {
        "status_code": salud.status_code,
        "cuerpo": _cuerpo_json(salud),
        "hsts": salud.headers.get("strict-transport-security"),
    }
    if token:
        detalle = cliente.get(f"{base}/health/detalle", headers={"Authorization": f"Bearer {token}"})
        evidencia["health_detalle"] = {"status_code": detalle.status_code, "cuerpo": _cuerpo_json(detalle)}
    else:
        evidencia["health_detalle"] = {"status_code": None, "cuerpo": None, "error": "sin EVIDENCIA_TOKEN"}
    evidencia["docs_publicados"] = {
        ruta: cliente.get(f"{base}{ruta}").status_code for ruta in ("/docs", "/openapi.json")
    }
    return evidencia


def recolectar(
    base: str, token: str | None, transport: httpx.BaseTransport | None = None, raiz: Path | None = None
) -> dict:
    base = base.rstrip("/")
    evidencia: dict = {
        "backend_url": base,
        "https": base.startswith("https://"),
        "tomada_en": datetime.now(tz=timezone.utc).isoformat(),
    }
    try:
        with httpx.Client(timeout=TIMEOUT_SEGUNDOS, follow_redirects=False, transport=transport) as cliente:
            evidencia.update(_consultar(cliente, base, token))
    except httpx.HTTPError as exc:
        # Sin red o backend caído: la evidencia lo dice, sin traza ni token.
        evidencia["error_conexion"] = f"{type(exc).__name__}: {exc}"
    repositorio = comandos_de_arranque(raiz) if raiz is not None else comandos_de_arranque()
    evidencia["configuracion_repositorio"] = {"comando_esperado": COMANDO, "comandos": repositorio}

    cuerpo = (evidencia.get("health_detalle") or {}).get("cuerpo") or {}
    arranque = cuerpo.get("arranque") or {}
    proxy = cuerpo.get("proxy") or {}
    docs = evidencia.get("docs_publicados") or {}
    evidencia["verificaciones"] = {
        "https": evidencia["https"],
        "liveness_ok": (evidencia.get("health") or {}).get("status_code") == 200,
        "db_ok": cuerpo.get("db") == "ok",
        "migraciones_aplicadas": bool(cuerpo.get("alembic_revision")),
        "mqtt_conectado": bool((cuerpo.get("mqtt") or {}).get("conectado")),
        "arranque_reproducible": arranque.get("lanzador") == LANZADOR and arranque.get("workers") == WORKERS,
        "proxy_ve_https": proxy.get("esquema") == "https",
        "ip_cliente_publica": _ip_publica(proxy.get("ip_cliente")),
        "docs_ocultos": bool(docs) and all(c == 404 for c in docs.values()),
        "repositorio_arranque_unico": all(c == COMANDO for c in repositorio.values()),
    }
    return evidencia


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evidencia_despliegue", description=__doc__.splitlines()[0])
    parser.add_argument("--salida", type=Path, default=Path("docs/evidencias/HU-55_despliegue.json"))
    args = parser.parse_args(argv)
    base = os.environ.get("BACKEND_URL")
    if not base:
        print("Defina BACKEND_URL", file=sys.stderr)
        return 2
    evidencia = recolectar(base, os.environ.get("EVIDENCIA_TOKEN"), transport=transport)
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(json.dumps(evidencia, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(evidencia["verificaciones"], indent=2))
    if "error_conexion" in evidencia:
        print(f"Sin conexión con {base}: {evidencia['error_conexion']}", file=sys.stderr)
    return 0 if all(evidencia["verificaciones"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
