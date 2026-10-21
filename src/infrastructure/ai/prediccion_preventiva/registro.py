"""Registro versionado de modelos preventivos.

Estructura (dentro de la imagen del backend, como el modelo legado):

    src/infrastructure/ai/models/preventivo/
        historial.json                 # bitácora de solo anexado
        <version>/modelo.joblib        # estimador sklearn
        <version>/manifiesto.json      # procedencia, métricas, umbral, estado, SHA-256

- Una versión registrada NUNCA se sobrescribe: registrar dos veces la misma
  versión falla. El rollback es seleccionar otra versión (variable
  IA_PREVENTIVO_VERSION), porque todas se conservan.
- El SHA-256 del artefacto se calcula sobre el archivo ya escrito y se
  verifica ANTES de deserializarlo: joblib/pickle ejecuta código al cargar,
  así que solo se cargan artefactos propios cuyo hash coincide.
- Cambiar el estado a APPROVED_FOR_DEPLOYMENT exige partir de
  VALIDATED_OFFLINE, evidencia del Experimento D (datos ColdCare) y la
  identidad de quien aprueba.
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import joblib

from src.infrastructure.ai.prediccion_preventiva.configuracion import (
    ESTADO_APROBADO,
    ESTADO_VALIDADO_OFFLINE,
    ESTADOS_MODELO,
)

RAIZ_REGISTRO = Path(__file__).resolve().parents[1] / "models" / "preventivo"
ARCHIVO_MODELO = "modelo.joblib"
ARCHIVO_MANIFIESTO = "manifiesto.json"
ARCHIVO_HISTORIAL = "historial.json"
PATRON_VERSION = re.compile(r"^[a-z0-9][a-z0-9.\-]{0,63}$")


class ErrorRegistro(RuntimeError):
    pass


def sha256_archivo(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as fh:
        for bloque in iter(lambda: fh.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def directorio_version(version: str, raiz: Path = RAIZ_REGISTRO) -> Path:
    """Valida el nombre antes de construir la ruta: una versión llega de una
    variable de entorno y no debe poder apuntar fuera del registro."""
    if not PATRON_VERSION.fullmatch(version):
        raise ErrorRegistro(f"Nombre de versión inválido: {version!r}")
    return raiz / version


def _escribir_json(ruta: Path, datos: dict | list) -> None:
    ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _anexar_historial(raiz: Path, entrada: dict) -> None:
    ruta = raiz / ARCHIVO_HISTORIAL
    historial = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else []
    historial.append(entrada)
    _escribir_json(ruta, historial)


def registrar(modelo, manifiesto: dict, raiz: Path = RAIZ_REGISTRO) -> Path:
    version = manifiesto["version"]
    if manifiesto.get("estado") not in ESTADOS_MODELO:
        raise ErrorRegistro(f"Estado desconocido: {manifiesto.get('estado')!r}")
    if manifiesto["estado"] == ESTADO_APROBADO:
        raise ErrorRegistro("Un modelo no se registra ya aprobado: la aprobación es un paso explícito posterior")
    destino = directorio_version(version, raiz)
    if destino.exists():
        raise ErrorRegistro(f"La versión {version} ya existe; el registro no sobrescribe versiones")
    destino.mkdir(parents=True)
    ruta_modelo = destino / ARCHIVO_MODELO
    joblib.dump(modelo, ruta_modelo, compress=3)
    manifiesto = {
        **manifiesto,
        "artefacto": {
            "archivo": ARCHIVO_MODELO,
            "formato": "joblib",
            "sha256": sha256_archivo(ruta_modelo),
            "bytes": ruta_modelo.stat().st_size,
        },
    }
    ruta_manifiesto = destino / ARCHIVO_MANIFIESTO
    _escribir_json(ruta_manifiesto, manifiesto)
    _anexar_historial(
        raiz,
        {
            "evento": "registrado",
            "version": version,
            "estado": manifiesto["estado"],
            "cuando": datetime.now(tz=timezone.utc).isoformat(),
            "sha256_artefacto": manifiesto["artefacto"]["sha256"],
            "sha256_manifiesto": sha256_archivo(ruta_manifiesto),
        },
    )
    return destino


def leer_manifiesto(version: str, raiz: Path = RAIZ_REGISTRO) -> dict:
    ruta = directorio_version(version, raiz) / ARCHIVO_MANIFIESTO
    if not ruta.exists():
        raise ErrorRegistro(f"No existe el manifiesto de la versión {version}")
    return json.loads(ruta.read_text(encoding="utf-8"))


def cargar_verificado(version: str, raiz: Path = RAIZ_REGISTRO, sha256_esperado: str | None = None):
    """(modelo, manifiesto) solo si el SHA-256 del archivo coincide con el
    manifiesto y, si se fijó, con el hash aprobado en la configuración del
    despliegue. Nunca deserializa un archivo que no pasó la verificación."""
    manifiesto = leer_manifiesto(version, raiz)
    ruta_modelo = directorio_version(version, raiz) / manifiesto["artefacto"]["archivo"]
    if ruta_modelo.name != ARCHIVO_MODELO or not ruta_modelo.exists():
        raise ErrorRegistro("Artefacto ausente o con nombre inesperado")
    real = sha256_archivo(ruta_modelo)
    if real != manifiesto["artefacto"]["sha256"]:
        raise ErrorRegistro("El SHA-256 del artefacto no coincide con su manifiesto; no se deserializa")
    if sha256_esperado is not None and real != sha256_esperado:
        raise ErrorRegistro("El SHA-256 del artefacto no coincide con el hash aprobado en la configuración")
    return joblib.load(ruta_modelo), manifiesto


def listar(raiz: Path = RAIZ_REGISTRO) -> list[dict]:
    if not raiz.exists():
        return []
    salida = []
    for directorio in sorted(p for p in raiz.iterdir() if p.is_dir()):
        try:
            m = leer_manifiesto(directorio.name, raiz)
        except ErrorRegistro:
            continue
        salida.append({"version": m["version"], "estado": m["estado"], "entrenado_en": m.get("entrenado_en")})
    return salida


def cambiar_estado(
    version: str,
    nuevo_estado: str,
    motivo: str,
    responsable: str,
    evidencia_experimento_d: Path | None = None,
    raiz: Path = RAIZ_REGISTRO,
) -> dict:
    if nuevo_estado not in ESTADOS_MODELO:
        raise ErrorRegistro(f"Estado desconocido: {nuevo_estado!r}")
    manifiesto = leer_manifiesto(version, raiz)
    anterior = manifiesto["estado"]
    if nuevo_estado == ESTADO_APROBADO:
        if anterior != ESTADO_VALIDADO_OFFLINE:
            raise ErrorRegistro(f"Solo un modelo {ESTADO_VALIDADO_OFFLINE} puede aprobarse (actual: {anterior})")
        if evidencia_experimento_d is None or not evidencia_experimento_d.exists():
            raise ErrorRegistro("Aprobar exige el informe del Experimento D con datos ColdCare reales")
        if not responsable.strip():
            raise ErrorRegistro("Aprobar exige identificar a la persona responsable")
    entrada = {
        "evento": "cambio_estado",
        "version": version,
        "estado_anterior": anterior,
        "estado_nuevo": nuevo_estado,
        "motivo": motivo,
        "responsable": responsable,
        "cuando": datetime.now(tz=timezone.utc).isoformat(),
    }
    if evidencia_experimento_d is not None:
        entrada["evidencia_experimento_d_sha256"] = sha256_archivo(evidencia_experimento_d)
    manifiesto["estado"] = nuevo_estado
    manifiesto.setdefault("historial_estados", []).append(entrada)
    ruta_manifiesto = directorio_version(version, raiz) / ARCHIVO_MANIFIESTO
    _escribir_json(ruta_manifiesto, manifiesto)
    _anexar_historial(raiz, {**entrada, "sha256_manifiesto": sha256_archivo(ruta_manifiesto)})
    return manifiesto
