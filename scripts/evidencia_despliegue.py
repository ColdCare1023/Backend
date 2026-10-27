"""HU-55: evidencia del despliegue en Railway (Definición de Hecho).

Uso:

    BACKEND_URL=https://<servicio>.up.railway.app EVIDENCIA_TOKEN=<JWT de administrador> \
    python -m scripts.evidencia_despliegue --salida docs/evidencias/HU-55_despliegue.json

Comprueba y deja en un JSON fechado:
- que la URL es HTTPS y responde `/health` (liveness, sin base de datos);
- `/health/detalle` (requiere un JWT de ADMINISTRADOR): base de datos,
  revisión de Alembic, MQTT, despachador, vigilancia SIN_DATOS y lo que el
  backend ve detrás del proxy (esquema https e IP real del cliente, B-11);
- que `/docs` y `/openapi.json` NO están publicados en producción.
El token se lee del entorno y nunca se escribe en la evidencia.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx


def recolectar(base: str, token: str | None) -> dict:
    base = base.rstrip("/")
    evidencia: dict = {"backend_url": base, "https": base.startswith("https://"),
                       "tomada_en": datetime.now(tz=timezone.utc).isoformat()}
    with httpx.Client(timeout=15, follow_redirects=False) as cliente:
        salud = cliente.get(f"{base}/health")
        evidencia["health"] = {"status_code": salud.status_code, "cuerpo": salud.json() if salud.is_success else None,
                               "hsts": salud.headers.get("strict-transport-security")}
        if token:
            detalle = cliente.get(f"{base}/health/detalle", headers={"Authorization": f"Bearer {token}"})
            evidencia["health_detalle"] = {
                "status_code": detalle.status_code, "cuerpo": detalle.json() if detalle.is_success else None,
            }
        evidencia["docs_publicados"] = {
            ruta: cliente.get(f"{base}{ruta}").status_code for ruta in ("/docs", "/openapi.json")
        }
    cuerpo = (evidencia.get("health_detalle") or {}).get("cuerpo") or {}
    evidencia["verificaciones"] = {
        "https": evidencia["https"],
        "liveness_ok": evidencia["health"]["status_code"] == 200,
        "db_ok": cuerpo.get("db") == "ok",
        "migraciones_aplicadas": bool(cuerpo.get("alembic_revision")),
        "mqtt_conectado": bool((cuerpo.get("mqtt") or {}).get("conectado")),
        "proxy_ve_https": (cuerpo.get("proxy") or {}).get("esquema") == "https",
        "docs_ocultos": all(c == 404 for c in evidencia["docs_publicados"].values()),
    }
    return evidencia


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evidencia_despliegue")
    parser.add_argument("--salida", type=Path, default=Path("docs/evidencias/HU-55_despliegue.json"))
    args = parser.parse_args(argv)
    base = os.environ.get("BACKEND_URL")
    if not base:
        print("Defina BACKEND_URL", file=sys.stderr)
        return 2
    evidencia = recolectar(base, os.environ.get("EVIDENCIA_TOKEN"))
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(json.dumps(evidencia, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(evidencia["verificaciones"], indent=2))
    return 0 if all(evidencia["verificaciones"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
