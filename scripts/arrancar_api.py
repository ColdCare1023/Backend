"""HU-55 esc. 1 y 3 / HU-12 esc. 4: arranque único de la API en producción.

Uso (Dockerfile, Procfile, railpack.json y railway.json apuntan aquí):

    python -m scripts.arrancar_api

Railway puede construir con Dockerfile o con Railpack, y Railpack no lee el
`CMD` del Dockerfile ni el `railway.json` del repositorio. Antes, con Railpack,
el comando de arranque dependía de lo que se escribiera a mano en el panel, y
nada garantizaba un solo worker ni la confianza en el proxy. Este módulo es el
único lugar que fija esos parámetros, sea cual sea el builder:

- `workers=1` explícito: SSE, la cola de correo y el consumidor MQTT con
  `client_id` fijo viven en el proceso (README, «Topología»). Uvicorn tomaría
  `WEB_CONCURRENCY` del entorno si no se fijara aquí.
- `proxy_headers=True` y `forwarded_allow_ips` desde `FORWARDED_ALLOW_IPS`
  (por defecto 127.0.0.1, el mismo valor que tenía el `CMD` del Dockerfile).
  Railway termina TLS en su borde: sin confiar en ese proxy, el backend ve la
  IP del proxy y `http`, y la cuota de login por IP (HU-39) y la IP de la
  bitácora (HU-42/HU-50) quedan mal atribuidas.
- `PORT` lo asigna Railway (8000 en local).

Deja en el entorno `COLDCARE_ARRANQUE` y `COLDCARE_WORKERS` para que
`/health/detalle` muestre con qué comando arrancó realmente el proceso: si el
panel de Railway tuviera otro comando escrito a mano, se nota en la evidencia
(`scripts.evidencia_despliegue`).
"""

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path

APP = "src.interface.main:app"
LANZADOR = "scripts.arrancar_api"
COMANDO = f"python -m {LANZADOR}"
WORKERS = 1
PUERTO_POR_DEFECTO = 8000
FORWARDED_ALLOW_IPS_POR_DEFECTO = "127.0.0.1"
RAIZ = Path(__file__).resolve().parents[1]


def comandos_de_arranque(raiz: Path = RAIZ) -> dict[str, str | None]:
    """Comando de arranque que declara cada vía de despliegue del repositorio
    (None si el archivo no lo declara). Todas deben ser `COMANDO`: lo comprueban
    las pruebas y `scripts.evidencia_despliegue`, sin red ni credenciales."""

    def leer(nombre: str) -> str | None:
        ruta = raiz / nombre
        return ruta.read_text(encoding="utf-8") if ruta.is_file() else None

    procfile = leer("Procfile") or ""
    web = re.search(r"^web:\s*(.+?)\s*$", procfile, flags=re.MULTILINE)
    railpack = json.loads(leer("railpack.json") or "{}")
    railway = json.loads(leer("railway.json") or "{}")
    cmd = re.search(r"^CMD\s+(\[.*\])\s*$", leer("infra-local/backend/Dockerfile") or "", flags=re.MULTILINE)
    return {
        "Procfile": web.group(1) if web else None,
        "railpack.json": (railpack.get("deploy") or {}).get("startCommand"),
        "railway.json": (railway.get("deploy") or {}).get("startCommand"),
        "Dockerfile": " ".join(json.loads(cmd.group(1))) if cmd else None,
    }


def configuracion_uvicorn(entorno: Mapping[str, str] | None = None) -> dict:
    """Argumentos de `uvicorn.run` para producción. Sin efectos: se prueba sola."""
    entorno = os.environ if entorno is None else entorno
    puerto = (entorno.get("PORT") or "").strip()
    return {
        "app": APP,
        # El contenedor solo expone este puerto al proxy de la plataforma.
        "host": "0.0.0.0",
        "port": int(puerto) if puerto else PUERTO_POR_DEFECTO,
        "workers": WORKERS,
        "proxy_headers": True,
        "forwarded_allow_ips": (entorno.get("FORWARDED_ALLOW_IPS") or "").strip() or FORWARDED_ALLOW_IPS_POR_DEFECTO,
    }


def main() -> None:
    import uvicorn

    configuracion = configuracion_uvicorn()
    os.environ["COLDCARE_ARRANQUE"] = LANZADOR
    os.environ["COLDCARE_WORKERS"] = str(configuracion["workers"])
    # Con workers=1 uvicorn sirve en este mismo proceso (PID 1 en el
    # contenedor), así que recibe el SIGTERM del redespliegue sin intermediarios.
    uvicorn.run(**configuracion)


if __name__ == "__main__":
    main()
