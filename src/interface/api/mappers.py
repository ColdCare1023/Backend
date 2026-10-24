import math
from numbers import Real

from src.domain.entities.alerta_termica import AlertaTermica
from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.entities.registro_trazabilidad import RegistroTrazabilidad
from src.domain.entities.usuario import Usuario
from src.domain.value_objects.rol import Rol
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
        time_quality=lectura.time_quality,
        timestamp_estimado=lectura.timestamp_estimado,
        origen=lectura.origen,
        registrado_por=lectura.registrado_por,
        estado_sensores={
            "temperatura_interna": _estado_sensor(lectura.temperatura_interna, -55.0, 125.0),
            "temperatura_ambiental": _estado_sensor(lectura.temperatura_ambiental, -40.0, 125.0),
            "humedad_ambiental": _estado_sensor(lectura.humedad_ambiental, 0.0, 100.0),
        },
    )


NOMBRE_USUARIO_ANONIMIZADO = "Usuario desactivado"


def nombre_visible(usuario: Usuario | None) -> str | None:
    """D7 (HU-28/HU-41): nombre que la interfaz puede mostrar de un usuario.
    Uno desactivado y anonimizado (HU-45, Ley 29733) no se identifica, aunque su
    historial auditable siga intacto."""
    if usuario is None:
        return None
    if usuario.anonymized_for_gdpr:
        return NOMBRE_USUARIO_ANONIMIZADO
    return usuario.nombre


def alerta_to_response(alerta: AlertaTermica, revisada_por_nombre: str | None = None) -> AlertaResponse:
    return AlertaResponse(
        revisada_por_nombre=revisada_por_nombre,
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


# BE-005: campos con datos personales o de la bitácora administrativa que la
# cadena conserva (forman parte del hash y no se pueden borrar) pero que solo
# deben ver los roles con función de auditoría. Al resto se le muestran
# redactados; el hash y el resto del eslabón siguen siendo verificables.
CAMPOS_SENSIBLES_POR_EVENTO: dict[str, frozenset[str]] = {
    "AUDIT_LOG": frozenset({"detalle", "ip_origen"}),
    "ACEPTACION_PRIVACIDAD": frozenset({"email", "ip_origen"}),
    "RECHAZO_PRIVACIDAD": frozenset({"email", "ip_origen"}),
    "DESACTIVACION_USUARIO": frozenset({"ip_origen_admin"}),
    "RESPONSABLE_DISPOSITIVO_ACTUALIZADO": frozenset({"responsable_email", "responsable_telefono"}),
}
REDACTADO = "[redactado]"
ROLES_CON_VISTA_COMPLETA = frozenset({Rol.ADMINISTRADOR, Rol.AUDITOR})


def payload_visible(tipo_evento: str, payload: dict, rol: Rol | None) -> dict:
    """Payload del eslabón tal como lo puede ver `rol` (BE-005)."""
    sensibles = CAMPOS_SENSIBLES_POR_EVENTO.get(tipo_evento)
    if not sensibles or rol in ROLES_CON_VISTA_COMPLETA:
        return payload
    return {clave: (REDACTADO if clave in sensibles else valor) for clave, valor in payload.items()}


def trazabilidad_to_response(registro: RegistroTrazabilidad, rol: Rol | None = None) -> TrazabilidadResponse:
    """`rol=None` solo para usos internos sin destinatario humano; las rutas
    de la API siempre pasan el rol de quien consulta."""
    return TrazabilidadResponse(
        id=registro.id,
        tipo_evento=registro.tipo_evento,
        device_id=registro.device_id,
        usuario_id=registro.usuario_id,
        payload=payload_visible(registro.tipo_evento, registro.payload, rol) if rol is not None else registro.payload,
        timestamp=registro.timestamp,
        previous_hash=registro.previous_hash,
        hash_actual=registro.hash_actual,
        chain_id=registro.chain_id,
        chain_seq=registro.chain_seq,
    )
