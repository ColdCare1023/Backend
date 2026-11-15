"""HU-29: huella verificable de las tablas de evidencia.

Para comprobar que una restauración devolvió EXACTAMENTE lo respaldado no basta
con contar filas: se calcula, por tabla, el SHA-256 de sus filas en un orden
estable (por clave primaria) con las columnas que constituyen la evidencia.
La misma función corre sobre la base de origen (al respaldar) y sobre la
restaurada (en el entorno aislado); si cualquier valor difiere, la huella
difiere.

En el origen la huella se calcula DENTRO de la misma transacción REPEATABLE
READ cuyo snapshot usa `pg_dump --snapshot` (scripts/respaldo_postgres.py): las
filas que entren durante o después del volcado no están ni en el archivo ni en
la huella. Las tablas se recorren por clave (keyset), no con OFFSET: una fila
insertada entre dos páginas no puede duplicar ni saltar otra.
"""

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from sqlalchemy import text


class _Ejecutor(Protocol):
    """AsyncSession o AsyncConnection: solo se usa `execute`."""

    async def execute(self, sentencia, parametros=None): ...


# Columnas con las que se calcularon las huellas hasta HU-29 v2 (manifiestos con
# `columnas` de entonces y los anteriores sin ellas). Se congelan aquí: ampliar
# TABLAS_EVIDENCIA no debe cambiar cómo se verifica un respaldo ya hecho.
_LECTURAS_V2 = (
    "id", "device_id", "timestamp", "temperatura_interna", "temperatura_ambiental",
    "humedad_ambiental", "apertura_refrigerador", "reading_id", "boot_id", "seq_no",
    "riesgo_efectivo", "excursion_confirmada", "origen",
)
_TRAZABILIDAD_V2 = ("id", "tipo_evento", "device_id", "chain_id", "previous_hash", "hash_actual")

# Columnas de evidencia por tabla (HU-29 esc. 2: «se recuperan al 100 %»).
# `traceability_records` incluye el contenido del eslabón (payload, timestamp,
# chain_seq, versión de la fórmula y actor), no solo los hashes: una
# restauración que altere la evidencia sin tocar el hash se nota aquí. Las
# lecturas incluyen la evidencia de la inferencia (HU-18/HU-47).
TABLAS_EVIDENCIA: dict[str, tuple[str, ...]] = {
    "thermal_readings": (
        *_LECTURAS_V2,
        "nivel_riesgo", "estado_conectividad", "payload", "modelo_version", "confianza_ia",
        "origen_clasificacion", "estado_inferencia", "inferido_en", "motivo_no_inferencia",
        "schema_version", "time_quality", "received_at", "uptime_ms", "timestamp_estimado",
        "registrado_por", "probabilidades_ia", "vector_features_ia", "created_at",
    ),
    "traceability_records": (
        *_TRAZABILIDAD_V2,
        "payload", "timestamp", "chain_seq", "hash_version", "usuario_id", "created_at",
    ),
    # HU-42: `resultado` (exitoso/fallido/denegado/bloqueado, migración 0039)
    # es parte de la evidencia de la bitácora: una restauración que lo pierda
    # o lo altere debe notarse.
    "audit_logs": ("id", "usuario_id", "accion", "recurso", "detalle", "resultado", "created_at"),
    # HU-29 esc. 2: «los usuarios y la metadata crítica se recuperan al 100 %».
    # El hash de la contraseña entra en la huella (no se expone: solo aporta al
    # SHA-256): una restauración sin él dejaría a todos sin poder entrar.
    "users": (
        "id", "nombre", "email", "password_hash", "rol_id", "is_active", "motivo_desactivacion",
        "desactivado_en", "privacy_accepted", "privacy_version_accepted", "created_at",
    ),
    "devices": (
        "id", "nombre", "ubicacion", "activo", "es_demo", "motivo_baja", "dado_de_baja_en",
        "reemplaza_a_device_id", "fecha_ultima_calibracion", "numero_certificado_calibracion",
        "fecha_proxima_calibracion", "mqtt_token_hash", "mqtt_token_activo", "fecha_instalacion",
        "instalado_por", "created_at",
    ),
    "thermal_alerts": (
        "id", "reading_id", "device_id", "nivel_riesgo", "estado", "revisada_por", "lectura_inicial_id",
        "lectura_mas_reciente_id", "iniciada_en", "reconocida_en", "atendida_en", "cerrada_en",
        "temperatura_pico", "historica", "created_at",
    ),
    "corrective_actions": (
        "id", "alert_id", "usuario_id", "descripcion", "decision", "justificacion", "corrige_accion_id",
        "motivo_rectificacion", "created_at",
    ),
    "device_config_history": ("id", "device_id", "campo", "valor_anterior", "valor_nuevo", "actor_id", "created_at"),
    # HU-46/HU-47: registro de versiones del modelo IA (metadata crítica). La
    # historia de activaciones vive en traceability_records (payload).
    "ai_model_versions": (
        "id", "version", "model_hash", "feature_schema_version", "dataset_version",
        "scikit_learn_version", "python_version", "trained_at", "aprobado_por", "aprobado_en",
        "activa", "activada_en", "desactivada_en",
    ),
}
# Columnas con las que se calcularon las huellas de los manifiestos generados
# antes de que el manifiesto declarara las suyas (`columnas`): las tres tablas
# originales y `audit_logs` sin `resultado`. La verificación de un respaldo
# antiguo usa exactamente estas (ni una tabla más), o la comparación fallaría
# aunque la restauración fuera exacta.
TABLAS_EVIDENCIA_LEGADO: dict[str, tuple[str, ...]] = {
    "thermal_readings": _LECTURAS_V2,
    "traceability_records": _TRAZABILIDAD_V2,
    "audit_logs": ("id", "usuario_id", "accion", "recurso", "detalle", "created_at"),
}
LOTE = 5_000


def _normalizar(valor):
    """Representación estable entre PostgreSQL y su copia restaurada (y SQLite
    en pruebas): UUID sin guiones, instantes en UTC sin zona, floats a 6
    decimales, JSON con claves ordenadas."""
    if isinstance(valor, float):
        return round(valor, 6)
    if isinstance(valor, Decimal):
        return str(valor.normalize())
    if isinstance(valor, UUID):
        return valor.hex
    if isinstance(valor, datetime):
        if valor.tzinfo is not None:
            valor = valor.astimezone(timezone.utc).replace(tzinfo=None)
        return valor.isoformat(timespec="microseconds")
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, dict | list):
        return json.loads(json.dumps(valor, sort_keys=True, default=str))
    if isinstance(valor, str) and len(valor) == 36 and valor.count("-") == 4:
        try:
            return UUID(valor).hex
        except ValueError:
            return valor
    return valor


async def huella_tabla(conexion: _Ejecutor, tabla: str, columnas: tuple[str, ...]) -> dict:
    """Filas y SHA-256 de `tabla` en orden de clave primaria. Paginación por
    clave (`WHERE id > :ultima`), nunca OFFSET: dentro de un snapshot da lo
    mismo que una sola consulta, y fuera de él una fila nueva no desplaza las
    páginas. `filas` es el número de filas que entraron en el SHA-256."""
    lista = ", ".join(columnas)
    digest = hashlib.sha256()
    filas_leidas = 0
    ultima = None
    while True:
        if ultima is None:
            sentencia = text(f"SELECT id AS clave_huella, {lista} FROM {tabla} ORDER BY id LIMIT :lote")
            parametros = {"lote": LOTE}
        else:
            sentencia = text(
                f"SELECT id AS clave_huella, {lista} FROM {tabla} WHERE id > :ultima ORDER BY id LIMIT :lote"
            )
            parametros = {"lote": LOTE, "ultima": ultima}
        filas = (await conexion.execute(sentencia, parametros)).all()
        for fila in filas:
            digest.update(json.dumps([_normalizar(v) for v in fila[1:]], sort_keys=True, default=str).encode())
            digest.update(b"\n")
        filas_leidas += len(filas)
        if len(filas) < LOTE:
            break
        ultima = filas[-1][0]
    return {"filas": filas_leidas, "sha256": digest.hexdigest()}


async def huellas_evidencia(
    conexion: _Ejecutor, columnas: dict[str, tuple[str, ...] | list[str]] | None = None
) -> dict[str, dict]:
    """Huella por tabla. `columnas` (por omisión, TABLAS_EVIDENCIA) permite
    recalcularla con las columnas que declara un manifiesto anterior.
    `conexion` es una AsyncSession o una AsyncConnection; para el respaldo, la
    de la transacción cuyo snapshot exportado usa pg_dump."""
    tablas = columnas if columnas is not None else TABLAS_EVIDENCIA
    for tabla, cols in tablas.items():
        if tabla not in TABLAS_EVIDENCIA or not set(cols) <= set(TABLAS_EVIDENCIA[tabla]):
            # Los nombres se interpolan en SQL: solo los conocidos.
            raise ValueError(f"Columnas de huella no reconocidas para {tabla}")
    return {tabla: await huella_tabla(conexion, tabla, tuple(cols)) for tabla, cols in tablas.items()}


def columnas_de_manifiesto(manifiesto: dict) -> dict[str, tuple[str, ...]]:
    """Columnas con las que se calculó la huella de un manifiesto: las que
    declara, o las del formato anterior si no declara ninguna."""
    declaradas = manifiesto.get("columnas")
    if not declaradas:
        return TABLAS_EVIDENCIA_LEGADO
    return {tabla: tuple(cols) for tabla, cols in declaradas.items()}


def comparar_huellas(origen: dict[str, dict], restaurada: dict[str, dict]) -> dict:
    """Resultado de la prueba de restauración: por tabla, si coinciden filas y
    huella. `coincide` es True solo si coinciden TODAS las tablas."""
    detalle = {}
    for tabla in sorted(set(origen) | set(restaurada)):
        a, b = origen.get(tabla), restaurada.get(tabla)
        detalle[tabla] = {
            "filas_origen": a["filas"] if a else None,
            "filas_restauradas": b["filas"] if b else None,
            "coinciden_filas": bool(a and b and a["filas"] == b["filas"]),
            "coincide_huella": bool(a and b and a["sha256"] == b["sha256"]),
        }
    return {
        "coincide": all(d["coinciden_filas"] and d["coincide_huella"] for d in detalle.values()),
        "tablas": detalle,
    }
