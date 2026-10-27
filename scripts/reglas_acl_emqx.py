"""B-2 (HU-10): reglas ACL exactas para cargar en la consola de EMQX Cloud
Serverless, sin plantillas.

`infra-local/emqx/acl.conf` usa el marcador `${username}`, que el EMQX local
admite. En Serverless las reglas se cargan en la consola (Access Control →
Authorization) y el marcador no está confirmado para esa edición, así que se
cargan reglas explícitas por dispositivo. Este script las genera a partir de
los dispositivos activos de la base (o de `--device`) para no escribirlas a mano.

    python -m scripts.reglas_acl_emqx                    # desde DATABASE_URL
    python -m scripts.reglas_acl_emqx --device FARM-01 --device FARM-02 --formato csv

Cada alta o baja de dispositivo exige volver a cargar sus reglas (y queda
pendiente en el backend hasta confirmarlo, ver HU-43/HU-44).
"""

import argparse
import asyncio
import csv
import io
import json
import sys

USUARIO_BACKEND = "backend_service"


def reglas(dispositivos: list[str], usuario_backend: str = USUARIO_BACKEND) -> list[dict]:
    filas = [
        {"usuario": usuario_backend, "accion": "subscribe", "topico": "farmacias/+/lecturas", "permiso": "allow"},
        {"usuario": usuario_backend, "accion": "subscribe", "topico": "farmacias/+/eventos", "permiso": "allow"},
        {"usuario": usuario_backend, "accion": "publish", "topico": "farmacias/+/ack", "permiso": "allow"},
        {"usuario": usuario_backend, "accion": "publish", "topico": "farmacias/+/ota", "permiso": "allow"},
    ]
    for device_id in sorted(set(dispositivos)):
        filas += [
            {"usuario": device_id, "accion": "publish", "topico": f"farmacias/{device_id}/lecturas", "permiso": "allow"},
            {"usuario": device_id, "accion": "publish", "topico": f"farmacias/{device_id}/eventos", "permiso": "allow"},
            {"usuario": device_id, "accion": "subscribe", "topico": f"farmacias/{device_id}/ack", "permiso": "allow"},
            {"usuario": device_id, "accion": "subscribe", "topico": f"farmacias/{device_id}/ota", "permiso": "allow"},
        ]
    filas.append({"usuario": "(todos)", "accion": "all", "topico": "#", "permiso": "deny"})
    return filas


async def _dispositivos_activos() -> list[str]:
    from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
    from src.infrastructure.database.session import _session_factory

    async with _session_factory() as session:
        return [d["id"] for d in await SQLAlchemyDeviceRepository(session).listar() if d.get("activo")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reglas_acl_emqx")
    parser.add_argument("--device", action="append", default=None, help="device_id (repetible)")
    parser.add_argument("--formato", choices=("tabla", "csv", "json"), default="tabla")
    args = parser.parse_args(argv)
    dispositivos = args.device if args.device else asyncio.run(_dispositivos_activos())
    filas = reglas(dispositivos)
    if args.formato == "json":
        print(json.dumps(filas, indent=2))
    elif args.formato == "csv":
        salida = io.StringIO()
        escritor = csv.DictWriter(salida, fieldnames=["usuario", "accion", "topico", "permiso"])
        escritor.writeheader()
        escritor.writerows(filas)
        print(salida.getvalue(), end="")
    else:
        for f in filas:
            print(f"{f['permiso']:<6} {f['usuario']:<20} {f['accion']:<10} {f['topico']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
