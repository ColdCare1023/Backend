import math
from numbers import Real

from src.domain.entities.alerta_termica import AlertaTermica
from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.entities.registro_trazabilidad import RegistroTrazabilidad
from src.interface.api.schemas import AlertaResponse, LecturaResponse, TrazabilidadResponse


def _estado_sensor(valor: object | None, minimo: float, maximo: float) -> str:
    if valor is None:
        return "ausente"
    if not isinstance(valor, Real) or isinstance(valor, bool) or not math.isfinite(valor):
        return "invalido"
    if not minimo <= valor <= maximo:
        return "fisicamente_imposible"
    return "valido"


def evidencia_edge(
    *, firmware_version: str | None, duracion_apertura_segundos: int
) -> dict[str, object]:
    """Campos que el nodo edge reporta pero que no tienen columna propia en
    `thermal_readings`. Se guardan en la columna JSONB `payload` (HU-22) para
    que la evidencia no se pierda: antes se validaban y se descartaban."""
    return {
        "firmware_version": firmware_version,
        "duracion_apertura_segundos": duracion_apertura_segundos,
    }


def _duracion_apertura(payload: dict | None) -> int | None:
    """HU-35: extrae `duracion_apertura_segundos` de la evidencia edge
    guardada en `payload` (ver `evidencia_edge()` arriba). Devuelve None si
    falta o no es un entero — nunca 0 como valor inventado."""
    if not payload:
        return None
    valor = payload.get("duracion_apertura_segundos")
    return valor if isinstance(valor, int) and not isinstance(valor, bool) else None


def lectura_to_response(lectura: LecturaTermica) -> LecturaResponse:
    return LecturaResponse(
        id=lectura.id,
        device_id=lectura.device_id,
        timestamp=lectura.timestamp,
        temperatura_ambiental=lectura.temperatura_ambiental,
        humedad_ambiental=lectura.humedad_ambiental,
        temperatura_interna=lectura.temperatura_interna,
        apertura_refrigerador=lectura.apertura_refrigerador,
        duracion_apertura_segundos=_duracion_apertura(lectura.payload),
        estado_conectividad=lectura.estado_conectividad,
        nivel_riesgo=lectura.nivel_riesgo,
        confianza_ia=lectura.confianza_ia,
        modelo_version=lectura.modelo_version,
        model_version=lectura.modelo_version,
        origen_clasificacion=lectura.origen_clasificacion,
        estado_inferencia=lectura.estado_inferencia,
        motivo_no_inferencia=lectura.motivo_no_inferencia,
        reading_id=lectura.reading_id,
        schema_version=lectura.schema_version,
        excursion_confirmada=lectura.excursion_confirmada,
        riesgo_efectivo=lectura.riesgo_efectivo,
        estado_sensores={
            "temperatura_interna": _estado_sensor(lectura.temperatura_interna, -55.0, 125.0),
            "temperatura_ambiental": _estado_sensor(lectura.temperatura_ambiental, -40.0, 125.0),
            "humedad_ambiental": _estado_sensor(lectura.humedad_ambiental, 0.0, 100.0),
        },
    )


def alerta_to_response(alerta: AlertaTermica) -> AlertaResponse:
    return AlertaResponse(
        id=alerta.id,
        reading_id=alerta.reading_id,
        device_id=alerta.device_id,
        nivel_riesgo=alerta.nivel_riesgo,
        mensaje=alerta.mensaje,
        revisada=alerta.revisada,
        revisada_por=alerta.revisada_por,
        created_at=alerta.created_at,
        episodio_abierto=alerta.episodio_abierto,
        lectura_inicial_id=alerta.lectura_inicial_id,
        lectura_mas_reciente_id=alerta.lectura_mas_reciente_id,
        ultima_actualizacion=alerta.ultima_actualizacion,
        cerrada_en=alerta.cerrada_en,
        estado=alerta.estado.value,
        reconocida_en=alerta.reconocida_en,
        atendida_en=alerta.atendida_en,
    )


def trazabilidad_to_response(registro: RegistroTrazabilidad) -> TrazabilidadResponse:
    return TrazabilidadResponse(
        id=registro.id,
        tipo_evento=registro.tipo_evento,
        device_id=registro.device_id,
        usuario_id=registro.usuario_id,
        payload=registro.payload,
        timestamp=registro.timestamp,
        previous_hash=registro.previous_hash,
        hash_actual=registro.hash_actual,
        chain_id=registro.chain_id,
        chain_seq=registro.chain_seq,
    )
