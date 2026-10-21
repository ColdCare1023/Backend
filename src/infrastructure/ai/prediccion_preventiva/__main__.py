"""CLI offline del pipeline preventivo (nunca se expone por HTTP).

    python -m src.infrastructure.ai.prediccion_preventiva auditar
    python -m src.infrastructure.ai.prediccion_preventiva entrenar [--sin-registro] [--version V]
    python -m src.infrastructure.ai.prediccion_preventiva listar
    python -m src.infrastructure.ai.prediccion_preventiva verificar --version V
    python -m src.infrastructure.ai.prediccion_preventiva evaluar-coldcare --csv lecturas.csv --version V
    python -m src.infrastructure.ai.prediccion_preventiva estado --version V --nuevo ESTADO --motivo M \
        --responsable R [--evidencia informe_experimento_d.json]

Requiere el entorno offline (requirements-ml.txt) para las figuras.
"""

import argparse
import json
import sys
from pathlib import Path

from src.infrastructure.ai.prediccion_preventiva import registro
from src.infrastructure.ai.prediccion_preventiva.configuracion import ESTADOS_MODELO


def _imprimir(datos) -> None:
    print(json.dumps(datos, indent=2, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prediccion_preventiva")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("auditar", help="Auditoría reproducible de los datasets externos")
    p = sub.add_parser("entrenar", help="Auditoría + experimentos A1–A3, B, C + registro del modelo A1")
    p.add_argument("--sin-registro", action="store_true", help="No registrar el modelo (verificación de reproducibilidad)")
    p.add_argument("--version", default=None, help="Sufijo semántico de las versiones registradas (por defecto 1.0.0)")
    sub.add_parser("listar", help="Versiones registradas")
    p = sub.add_parser("verificar", help="Verifica el SHA-256 de una versión sin activarla")
    p.add_argument("--version", required=True)
    p = sub.add_parser("evaluar-coldcare", help="Experimento D: evalúa una versión con lecturas reales exportadas")
    p.add_argument("--csv", type=Path, required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--salida", type=Path, default=None)
    p = sub.add_parser("estado", help="Cambia el estado de una versión (queda en el historial)")
    p.add_argument("--version", required=True)
    p.add_argument("--nuevo", required=True, choices=ESTADOS_MODELO)
    p.add_argument("--motivo", required=True)
    p.add_argument("--responsable", required=True)
    p.add_argument("--evidencia", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.comando == "auditar":
        from src.infrastructure.ai.prediccion_preventiva.experimentos import ejecutar_auditoria

        _, _, auditoria, _ = ejecutar_auditoria()
        _imprimir({k: v["eventos"] for k, v in auditoria.items() if isinstance(v, dict) and "eventos" in v})
    elif args.comando == "entrenar":
        from src.infrastructure.ai.prediccion_preventiva.experimentos import ejecutar_todo

        resumen = ejecutar_todo(registrar_modelo=not args.sin_registro, version=args.version)
        _imprimir({
            "estado_modelo_principal": resumen["estado_modelo_principal"],
            "criterios": resumen["criterios"]["evaluacion"],
            "estado_variante_puerta": resumen["estado_variante_puerta"],
            "criterios_variante_puerta": resumen["criterios_variante_puerta"]["evaluacion"],
            "modelos_registrados": resumen.get("modelos_registrados"),
        })
    elif args.comando == "listar":
        _imprimir(registro.listar())
    elif args.comando == "verificar":
        _, manifiesto = registro.cargar_verificado(args.version)
        _imprimir({"version": args.version, "sha256": manifiesto["artefacto"]["sha256"], "estado": manifiesto["estado"], "verificado": True})
    elif args.comando == "evaluar-coldcare":
        from src.infrastructure.ai.prediccion_preventiva.coldcare import evaluar_coldcare

        informe = evaluar_coldcare(args.csv, args.version)
        if args.salida:
            args.salida.write_text(json.dumps(informe, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        _imprimir({k: informe[k] for k in ("version", "series", "observaciones", "positivas", "incidentes_independientes", "resultado")})
    elif args.comando == "estado":
        manifiesto = registro.cambiar_estado(args.version, args.nuevo, args.motivo, args.responsable, args.evidencia)
        _imprimir({"version": args.version, "estado": manifiesto["estado"]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
