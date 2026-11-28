import math
from datetime import datetime, timezone
from numbers import Real

from src.application.use_cases.clasificar_riesgo_termico import MOTIVO_REGLA_DIRECTA
from src.application.use_cases.registrar_estado_nodo import (
    CAUSA_BUFFER_AGOTADO,
    DESCRIPCION_BUFFER_AGOTADO,
    VistaBufferLittleFS,
)
from src.domain.entities.alerta_termica import AlertaTermica
from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.entities.registro_trazabilidad import RegistroTrazabilidad
from src.domain.entities.serie_termica import EstadisticaMagnitud, SerieTermica
from src.domain.entities.usuario import Usuario
from src.domain.repositories.i_estado_nodo_repository import EpisodioBufferSaturado
from src.domain.value_objects.rol import Rol
from src.infrastructure.config import get_settings
from src.interface.api.schemas import (
    AlertaResponse,
    BrechaBufferResponse,
    BrechaSerieResponse,
    BucketSerieResponse,
    BufferLittleFSResponse,
    EstadisticaMagnitudResponse,
    IaSombraResponse,
    LecturaResponse,
    ResumenSerieResponse,
    SerieTermicaResponse,
    TrazabilidadResponse,
)


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


def _apertura_continua(payload: dict | None) -> int | None:
    """HU-35: segundos que la puerta lleva abierta sin cerrarse, según el
    nodo (`door_open_streak_s` del contrato v1). None si estaba cerrada."""
    valor = (payload or {}).get("door_open_streak_s")
    return valor if isinstance(valor, int) and not isinstance(valor, bool) and valor > 0 else None


def lectura_sin_hora(lectura: LecturaTermica) -> bool:
    """HU-07 (H2): arranque sin hora sincronizada todavía sin reanclar. El
    `timestamp` de la lectura es entonces la hora de llegada al backend."""
    return not lectura.es_manual and lectura.time_quality == "unsynced" and lectura.timestamp_estimado is None


def origen_hora(lectura: LecturaTermica) -> str:
    if lectura.es_manual:
        return "manual"
    if lectura.time_quality == "unsynced":
        return "recepcion" if lectura.timestamp_estimado is None else "reconstruida"
    return "nodo"


def _ia_sombra(payload: dict | None) -> IaSombraResponse | None:
    """HU-34 esc. 2: la clase preventiva en sombra, si la lectura la trae."""
    valor = (payload or {}).get("ia_sombra")
    if not isinstance(valor, dict) or valor.get("clase") not in ("normal", "riesgo_preventivo"):
        return None
    return IaSombraResponse(clase=valor["clase"], modelo_version=valor.get("modelo_version"))


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
        apertura_continua_segundos=_apertura_continua(lectura.payload),
        umbral_puerta_abierta_segundos=(
            get_settings().puerta_umbral_prolongada_segundos if lectura.apertura_refrigerador is not None else None
        ),
        estado_conectividad=lectura.estado_conectividad,
        nivel_riesgo=lectura.nivel_riesgo,
        confianza_ia=lectura.confianza_ia,
        modelo_version=lectura.modelo_version,
        model_version=lectura.modelo_version,
        origen_clasificacion=lectura.origen_clasificacion,
        estado_inferencia=lectura.estado_inferencia,
        motivo_no_inferencia=lectura.motivo_no_inferencia,
        ia_experimental=bool((lectura.payload or {}).get("ia_experimental")),
        ia_sombra=_ia_sombra(lectura.payload),
        reading_id=lectura.reading_id,
        schema_version=lectura.schema_version,
        excursion_confirmada=lectura.excursion_confirmada,
        riesgo_efectivo=lectura.riesgo_efectivo,
        estado_riesgo=lectura.estado_riesgo(),
        time_quality=lectura.time_quality,
        timestamp_estimado=lectura.timestamp_estimado,
        origen=lectura.origen,
        registrado_por=lectura.registrado_por,
        sin_hora=lectura_sin_hora(lectura),
        origen_hora=origen_hora(lectura),
        recibida_en=lectura.received_at,
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


def tipo_sse_lectura(lectura: LecturaTermica) -> str:
    """Tipo del evento SSE de una lectura nueva. Una inferencia omitida por la regla directa
    (lectura fuera de 2-8 °C) sigue siendo una `lectura` normal para el tablero: solo el
    modelo ausente o el sensor en falla se anuncian aparte."""
    if lectura.estado_inferencia == "omitida" and lectura.motivo_no_inferencia != MOTIVO_REGLA_DIRECTA:
        return "fallo_sensor" if lectura.origen_clasificacion == "fallo_sensor" else "inferencia_omitida"
    return "lectura"


def tipo_sse_episodio(episodio_previo: AlertaTermica | None, episodio_actual: AlertaTermica | None) -> str | None:
    """Tipo del evento SSE del episodio de alerta tras una lectura; mismo
    criterio en la ingesta MQTT y en la HTTP. `episodio_previo` y
    `episodio_actual` son el episodio abierto del dispositivo antes y después
    de registrar la lectura.

    - `alerta`: hay un episodio abierto que no es el que estaba abierto antes.
      Incluye la alerta PENDIENTE nueva tras cerrar una ATENDIDA sin
      continuidad (IA-D18), el escalamiento preventiva → crítica y el cambio
      de nivel inverso (IA-D07), y la reapertura dentro de la ventana de un
      episodio no atendido (antes no había ninguno abierto). El tablero solo
      avisa de una alerta nueva con este evento.
    - `episodio_actualizado`: sigue abierto el mismo episodio.
    - `recuperacion`: había un episodio abierto y ya no queda ninguno.
    - None: sin episodio antes ni después (solo sale el evento de la lectura)."""
    if episodio_actual is None:
        return "recuperacion" if episodio_previo is not None else None
    if episodio_previo is None or episodio_previo.id != episodio_actual.id:
        return "alerta"
    return "episodio_actualizado"


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
        notificada_en=alerta.notificada_en,
        notificacion_intentos=alerta.notificacion_intentos,
        iniciada_en=alerta.iniciada_en,
        temperatura_pico=alerta.temperatura_pico,
        duracion_s=alerta.duracion_s,
        historica=alerta.historica,
        normalizacion_desde=alerta.normalizacion_desde,
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


# ── HU-31 / HU-36: serie agregada ──────────────────────────────────────────


def _utc(instante: datetime | None) -> datetime | None:
    if instante is None:
        return None
    return instante if instante.tzinfo else instante.replace(tzinfo=timezone.utc)


def _estadistica(e: EstadisticaMagnitud) -> EstadisticaMagnitudResponse:
    return EstadisticaMagnitudResponse(min=e.minimo, max=e.maximo, promedio=e.promedio, n=e.n)


def serie_to_response(serie: SerieTermica) -> SerieTermicaResponse:
    r = serie.resumen
    return SerieTermicaResponse(
        device_id=serie.device_id,
        desde=serie.desde,
        hasta=serie.hasta,
        bucket_segundos=serie.bucket_segundos,
        bucket_automatico=serie.bucket_automatico,
        intervalo_reporte_segundos=serie.intervalo_reporte_segundos,
        umbral_brecha_segundos=serie.umbral_brecha_segundos,
        total_lecturas=r.lecturas,
        lecturas_sin_hora=r.lecturas_sin_hora,
        lecturas_manuales=r.lecturas_manuales,
        buckets=[
            BucketSerieResponse(
                inicio=b.inicio,
                fin=b.fin,
                lecturas=b.lecturas,
                temperatura_interna=_estadistica(b.temperatura_interna),
                temperatura_ambiental=_estadistica(b.temperatura_ambiental),
                humedad_ambiental=_estadistica(b.humedad_ambiental),
                fuera_de_rango=b.fuera_de_rango,
                excursiones=b.excursiones,
            )
            for b in serie.buckets
        ],
        brechas=[
            BrechaSerieResponse(
                desde=_utc(b.desde),
                hasta=_utc(b.hasta),
                duracion_segundos=b.duracion_segundos,
                causa=b.causa,
                perdidas=b.perdidas,
                abierta=b.abierta,
            )
            for b in serie.brechas
        ],
        brechas_truncadas=serie.brechas_truncadas,
        resumen=ResumenSerieResponse(
            lecturas=r.lecturas,
            temperatura_interna=_estadistica(r.temperatura_interna),
            temperatura_ambiental=_estadistica(r.temperatura_ambiental),
            humedad_ambiental=_estadistica(r.humedad_ambiental),
            fuera_de_rango=r.fuera_de_rango,
            excursiones=r.excursiones,
            por_riesgo_efectivo=r.por_riesgo_efectivo,
            primera_lectura_en=r.primera_lectura_en,
            ultima_lectura_en=r.ultima_lectura_en,
        ),
    )


# ── HU-48: buffer LittleFS del nodo ────────────────────────────────────────


def brecha_buffer_to_response(episodio: EpisodioBufferSaturado) -> BrechaBufferResponse:
    return BrechaBufferResponse(
        desde=_utc(episodio.desde),
        hasta=_utc(episodio.hasta),
        muestras_perdidas=episodio.perdidas,
        perdidas=episodio.perdidas,
        causa=CAUSA_BUFFER_AGOTADO,
        causa_nodo=episodio.causa,
        descripcion=DESCRIPCION_BUFFER_AGOTADO,
        abierta=episodio.abierto,
        registrada_en=_utc(episodio.registrado_en),
        actualizada_en=_utc(episodio.actualizado_en),
        cerrada_en=_utc(episodio.cerrado_en),
        desde_boot_id=episodio.desde_boot_id,
        desde_seq_no=episodio.desde_seq_no,
        hasta_boot_id=episodio.hasta_boot_id,
        hasta_seq_no=episodio.hasta_seq_no,
    )


def buffer_to_response(vista: VistaBufferLittleFS | None) -> BufferLittleFSResponse | None:
    if vista is None:
        return None
    return BufferLittleFSResponse(
        pendientes=vista.pendientes,
        limite=vista.limite,
        ocupacion_pct=vista.ocupacion_pct,
        desde=_utc(vista.desde),
        hasta=_utc(vista.hasta),
        rango_estimado=vista.rango_estimado,
        perdidas=vista.perdidas,
        cuarentena=vista.cuarentena,
        reportado_en=vista.reportado_en,
        ultima_sincronizacion_en=vista.ultima_sincronizacion_en,
        brechas=[brecha_buffer_to_response(e) for e in vista.brechas],
    )
