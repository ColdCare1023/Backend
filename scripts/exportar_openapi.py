"""B-12: exporta el contrato OpenAPI del backend para las pruebas de contrato
del frontend (Frontend/src/tests/contrato/openapi-backend.json).

    python -m scripts.exportar_openapi ../Frontend/src/tests/contrato/openapi-backend.json

Regenerarlo cada vez que cambie un esquema de petición o respuesta: la prueba
del frontend falla si un formulario envía campos que este contrato rechaza.
"""

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("MQTT_ENABLED", "false")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    destino = Path(argv[0]) if argv else Path("openapi.json")
    from src.interface.main import create_app

    esquema = create_app().openapi()
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(esquema, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(f"OpenAPI {esquema['info']['version']} → {destino} ({len(esquema['paths'])} rutas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
