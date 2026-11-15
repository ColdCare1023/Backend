"""CLI offline del pipeline preventivo (nunca se expone por HTTP).

    python -m src.infrastructure.ai.prediccion_preventiva auditar
    python -m src.infrastructure.ai.prediccion_preventiva entrenar [--sin-registro] [--version V]
    python -m src.infrastructure.ai.prediccion_preventiva listar
    python -m src.infrastructure.ai.prediccion_preventiva verificar --version V
    python -m src.infrastructure.ai.prediccion_preventiva evaluar-coldcare --csv lecturas.csv --version V
    python -m src.infrastructure.ai.prediccion_preventiva corridas --csv corridas.csv --prueba C3,C4 \
        [--validacion C2] [--salida f.json]
    python -m src.infrastructure.ai.prediccion_preventiva corridas --csv corridas.csv --prueba C3,C4 \
        [--validacion C2] --registrar --version V [--conjunto termico_minimo] [--dataset-version D]
    python -m src.infrastructure.ai.prediccion_preventiva estado --version V --nuevo ESTADO --motivo M \
        --responsable R [--evidencia informe_experimento_d.json]

`listar`, `verificar`, `estado` y `corridas --registrar` aceptan
`--directorio-registro DIR` (por defecto, src/infrastructure/ai/models/preventivo),
p. ej. para ensayar el registro en un directorio temporal antes de hacerlo en el repositorio.

Requiere el entorno offline (requirements-ml.txt) para las figuras.
"""

import argparse
import json
import sys
from pathlib import Path

from src.infrastructure.ai.prediccion_preventiva import registro
from src.infrastructure.ai.prediccion_preventiva.configuracion import ESTADOS_MODELO
from src.infrastructure.ai.prediccion_preventiva.features import CONJUNTO_TERMICO, CONJUNTOS


def _imprimir(datos) -> None:
    print(json.dumps(datos, indent=2, ensure_ascii=False, default=str))


def _lista(texto: str | None) -> list[str]:
    return [c.strip() for c in (texto or "").split(",") if c.strip()]


def _opcion_directorio_registro(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--directorio-registro", type=Path, default=None,
        help="Registro de modelos preventivos (por defecto, src/infrastructure/ai/models/preventivo)",
    )


def _raiz(args) -> Path:
    return args.directorio_registro or registro.RAIZ_REGISTRO


def _escribir_json(ruta: Path, datos) -> None:
    ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prediccion_preventiva")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("auditar", help="Auditoría reproducible de los datasets externos")
    p = sub.add_parser("entrenar", help="Auditoría + experimentos A1–A3, B, C + registro del modelo A1")
    p.add_argument("--sin-registro", action="store_true", help="No registrar el modelo (verificación de reproducibilidad)")
    p.add_argument("--version", default=None, help="Sufijo semántico de las versiones registradas (por defecto 1.0.0)")
    p = sub.add_parser("listar", help="Versiones registradas")
    _opcion_directorio_registro(p)
    p = sub.add_parser("verificar", help="Verifica el SHA-256 de una versión sin activarla")
    p.add_argument("--version", required=True)
    _opcion_directorio_registro(p)
    p = sub.add_parser("evaluar-coldcare", help="Experimento D: evalúa una versión con lecturas reales exportadas")
    p.add_argument("--csv", type=Path, required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--salida", type=Path, default=None)
    p = sub.add_parser(
        "corridas",
        help="HU-17/18: entrena y evalúa con corridas controladas (con y sin SHT31); con --registrar, "
        "registra el modelo (P-2)",
    )
    p.add_argument("--csv", type=Path, required=True)
    p.add_argument("--prueba", required=True, help="corrida_id de prueba separados por coma")
    p.add_argument(
        "--validacion", default=None,
        help="corrida_id de validación separados por coma: eligen el umbral y no ajustan el modelo (P-3). "
        "Sin ellas, el umbral se elige dejando una corrida fuera",
    )
    p.add_argument("--horizonte", type=int, default=30, help="H en minutos")
    p.add_argument("--salida", type=Path, default=None)
    p.add_argument(
        "--registrar", action="store_true",
        help="Registra el modelo del conjunto elegido en models/preventivo/<V>/ como EXPERIMENTAL (P-2)",
    )
    p.add_argument("--version", default=None, help="Versión a registrar (obligatoria con --registrar, ≤ 50)")
    p.add_argument("--conjunto", default=CONJUNTO_TERMICO, choices=CONJUNTOS, help="Conjunto de features a registrar")
    p.add_argument(
        "--dataset-version", default=None,
        help="dataset_version del registro (≤ 50); por defecto corridas-<12 hex del SHA-256 del CSV>",
    )
    _opcion_directorio_registro(p)
    p = sub.add_parser("estado", help="Cambia el estado de una versión (queda en el historial)")
    p.add_argument("--version", required=True)
    p.add_argument("--nuevo", required=True, choices=ESTADOS_MODELO)
    p.add_argument("--motivo", required=True)
    p.add_argument("--responsable", required=True)
    p.add_argument("--evidencia", type=Path, default=None)
    _opcion_directorio_registro(p)
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
        _imprimir(registro.listar(_raiz(args)))
    elif args.comando == "verificar":
        _, manifiesto = registro.cargar_verificado(args.version, _raiz(args))
        _imprimir({"version": args.version, "sha256": manifiesto["artefacto"]["sha256"], "estado": manifiesto["estado"], "verificado": True})
    elif args.comando == "evaluar-coldcare":
        from src.infrastructure.ai.prediccion_preventiva.coldcare import evaluar_coldcare

        informe = evaluar_coldcare(args.csv, args.version)
        if args.salida:
            args.salida.write_text(json.dumps(informe, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        _imprimir({k: informe[k] for k in ("version", "series", "observaciones", "positivas", "incidentes_independientes", "resultado")})
    elif args.comando == "corridas":
        return _corridas(parser, args)
    elif args.comando == "estado":
        manifiesto = registro.cambiar_estado(
            args.version, args.nuevo, args.motivo, args.responsable, args.evidencia, raiz=_raiz(args)
        )
        _imprimir({"version": args.version, "estado": manifiesto["estado"]})
    return 0


def _corridas(parser: argparse.ArgumentParser, args) -> int:
    from src.infrastructure.ai.prediccion_preventiva.configuracion import ConfiguracionPreventiva
    from src.infrastructure.ai.prediccion_preventiva.corridas import ejecutar_pipeline_corridas, sin_privados

    if args.registrar and not args.version:
        parser.error("--registrar exige --version")
    if args.version and not args.registrar:
        parser.error("--version solo se usa con --registrar (sin --registrar, `corridas` es un informe)")
    config = ConfiguracionPreventiva(horizonte_min=args.horizonte)
    prueba, validacion = _lista(args.prueba), _lista(args.validacion) or None
    if args.registrar:
        from src.infrastructure.ai.prediccion_preventiva.registro_corridas import (
            RegistroRechazado,
            registrar_desde_corridas,
        )

        try:
            resumen = registrar_desde_corridas(
                args.csv, prueba, args.version, args.conjunto, config, validacion,
                raiz=_raiz(args), dataset_version=args.dataset_version,
            )
        except RegistroRechazado as exc:
            print(f"No se registra el modelo: {exc}", file=sys.stderr)
            return 2
        if args.salida:
            _escribir_json(args.salida, sin_privados({**resumen["_informe"], "registro": resumen}))
        _imprimir(sin_privados(resumen))
        return 0

    try:
        informe = ejecutar_pipeline_corridas(args.csv, prueba, config, validacion)
    except ValueError as exc:  # corridas inexistentes, CSV sin el esquema, validación que es de prueba...
        print(f"No se evalúan las corridas: {exc}", file=sys.stderr)
        return 2
    if args.salida:
        _escribir_json(args.salida, sin_privados(informe))
    _imprimir(sin_privados({
        "corridas": informe["corridas"],
        "excluidas": informe["importacion"],
        "criterios": {c: r.get("criterios") for c, r in informe["resultados"].items()},
        "umbral": {c: r.get("seleccion_umbral") for c, r in informe["resultados"].items()},
        "ablacion_sht31": informe["ablacion_sht31"],
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
