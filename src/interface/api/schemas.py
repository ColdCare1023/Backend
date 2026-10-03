from datetime import date, datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.domain.value_objects.rol import Rol

PASSWORD_MIN_LENGTH = 10

# bcrypt solo considera los primeros 72 BYTES de la contraseña y descarta el
# resto en silencio. Con el antiguo tope de 128 caracteres, dos contraseñas que
# compartieran los primeros 72 bytes eran intercambiables al iniciar sesión, y
# el usuario no tenía forma de saberlo. Se rechaza en el borde en lugar de
# truncar: es preferible un 422 explícito a una contraseña que "funciona" con
# una cola distinta a la que se escribió.
PASSWORD_MAX_BYTES = 72


class _PeticionEstricta(BaseModel):
    """Base de todo cuerpo de petición de la API.

    `extra="forbid"` (OWASP API6, asignación masiva): un campo no declarado deja
    de ignorarse en silencio y pasa a ser un 422. Ningún caso de uso construye
    entidades desempaquetando el cuerpo recibido, así que no había una
    vulnerabilidad explotable; lo que sí había era que un cliente desalineado
    —un frontend que envía `rol` a un endpoint que no lo acepta— fallaba de
    forma invisible en vez de ruidosa. Es el mismo criterio que ya aplicaba el
    contrato MQTT (`LecturaPayload`), que sí lo declaraba.
    """

    model_config = ConfigDict(extra="forbid")


class LoginRequest(_PeticionEstricta):
    email: EmailStr
    password: str = Field(min_length=8)


class LoginGoogleRequest(_PeticionEstricta):
    """ID token que devuelve Google Identity Services en el navegador.

    El límite de 4096 evita que el endpoint acepte cuerpos arbitrariamente
    grandes antes siquiera de intentar verificar la firma; un ID token real
    ronda el kilobyte."""

    id_token: str = Field(min_length=1, max_length=4096)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    require_privacy_consent: bool = False


class SSETicketResponse(BaseModel):
    ticket: str


class PrivacidadResponse(BaseModel):
    privacy_accepted: bool
    privacy_version_accepted: str | None


class UsuarioCreateRequest(_PeticionEstricta):
    nombre: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=PASSWORD_MIN_LENGTH)
    rol: Rol

    @field_validator("password")
    @classmethod
    def _politica_password(cls, valor: str) -> str:
        if not any(c.isdigit() for c in valor) or not any(c.isalpha() for c in valor):
            raise ValueError("La contraseña debe combinar letras y números")
        # Se mide en bytes porque es lo que consume bcrypt: una contraseña con
        # acentos o emoji ocupa más bytes que caracteres tiene.
        if len(valor.encode("utf-8")) > PASSWORD_MAX_BYTES:
            raise ValueError(
                f"La contraseña no puede superar {PASSWORD_MAX_BYTES} bytes "
                "(bcrypt ignora en silencio lo que exceda ese límite)"
            )
        return valor


class UsuarioResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nombre: str
    email: str
    rol: Rol
    is_active: bool = True
    motivo_desactivacion: str | None = None
    desactivado_en: datetime | None = None


class DesactivarUsuarioRequest(_PeticionEstricta):
    motivo: str = Field(min_length=1, max_length=50)


class CambiarRolUsuarioRequest(_PeticionEstricta):
    """HU-41: petición para asignar/modificar el rol de un usuario existente."""

    rol: Rol


class LecturaIngestRequest(_PeticionEstricta):
    """Espejo de src.infrastructure.mqtt.payload_schema.LecturaPayload para ingesta vía REST."""

    device_id: str = Field(min_length=1, max_length=50)
    timestamp: datetime
    temperatura_ambiental: float | None = Field(default=None, ge=-40.0, le=125.0)
    humedad_ambiental: float | None = Field(default=None, ge=0.0, le=100.0)
    temperatura_interna: float | None = Field(default=None, ge=-55.0, le=125.0)
    apertura_refrigerador: bool = False
    # Mismos dominios que en LecturaPayload: la vía REST y la vía MQTT deben
    # aceptar exactamente lo mismo, o el contrato se bifurca según el transporte.
    estado_conectividad: Literal["online", "offline"] = "online"
    firmware_version: str | None = Field(default=None, max_length=20)
    duracion_apertura_segundos: int = Field(default=0, ge=0)
    reading_id: str | None = Field(default=None, max_length=100)
    schema_version: int | None = None
    boot_id: int | None = Field(default=None, ge=0)
    seq_no: int | None = Field(default=None, ge=0)
    time_quality: Literal["synced", "unsynced"] | None = None


class LecturaResponse(BaseModel):
    id: UUID | None = None
    device_id: str
    timestamp: datetime
    temperatura_ambiental: float | None
    humedad_ambiental: float | None
    temperatura_interna: float | None
    # HU-04: None cuando el dispositivo no tiene MC-38 instalado.
    apertura_refrigerador: bool | None
    # HU-35: duración acumulada de ESTA apertura, tal como la reporta el nodo
    # (ver evidencia_edge() más abajo) — sin este campo el frontend no podía
    # distinguir una apertura de 5 s de una de 10 min para avisar del umbral.
    # None si el dispositivo no reportó el campo (firmware desactualizado).
    duracion_apertura_segundos: int | None = None
    estado_conectividad: str
    nivel_riesgo: NivelRiesgo | None
    # Evidencia de la inferencia de IA (RNF-04, corrige hallazgo AI-07 de la
    # auditoría: el contrato SSE no exponía confianza ni versión del modelo).
    confianza_ia: float | None = None
    modelo_version: str | None = None
    # Alias en inglés requerido por integración externa; se mantiene
    # `modelo_version` para no romper clientes ya desplegados.
    model_version: str | None = None
    # AIV-04 (fase de corrección): procedencia y estado real de la
    # clasificación — antes solo auditables en base de datos, no en la API.
    origen_clasificacion: str | None = None
    estado_inferencia: str | None = None
    motivo_no_inferencia: str | None = None
    estado_sensores: dict[str, str] | None = None
    # HU-05: identidad lógica declarada por el firmware (None en payloads
    # de dispositivos aún no actualizados) y versión del contrato de payload.
    reading_id: str | None = None
    schema_version: int | None = None
    # HU-18/21/34: `nivel_riesgo` arriba sigue siendo model_class (la clase
    # cruda del pipeline IA/salvaguarda). `excursion_confirmada` y
    # `riesgo_efectivo` son los dos conceptos adicionales que el backlog
    # exige mantener separados — el frontend no debe inferir una excursión
    # confirmada a partir de nivel_riesgo=excursion_critica por sí solo.
    excursion_confirmada: bool = False
    riesgo_efectivo: NivelRiesgo | None = None


class AlertaResponse(BaseModel):
    id: UUID | None = None
    reading_id: UUID
    device_id: str
    nivel_riesgo: NivelRiesgo
    mensaje: str
    revisada: bool
    revisada_por: UUID | None = None
    created_at: datetime | None = None
    # AIV-02 (fase de corrección): evidencia de episodio, no de lectura aislada.
    episodio_abierto: bool = True
    lectura_inicial_id: UUID | None = None
    lectura_mas_reciente_id: UUID | None = None
    ultima_actualizacion: datetime | None = None
    cerrada_en: datetime | None = None
    # HU-23: máquina de estados pendiente/reconocida/atendida.
    estado: str = "pendiente"
    reconocida_en: datetime | None = None
    atendida_en: datetime | None = None


class AccionCorrectivaCreateRequest(_PeticionEstricta):
    descripcion: str = Field(min_length=1, max_length=2000)


class AccionCorrectivaRectificarRequest(_PeticionEstricta):
    """HU-28: nueva justificación que corrige la anterior."""

    descripcion: str = Field(min_length=1, max_length=2000)


class AccionCorrectivaResponse(BaseModel):
    id: UUID | None = None
    alert_id: UUID
    usuario_id: UUID
    descripcion: str
    created_at: datetime | None = None
    corrige_accion_id: UUID | None = None


class TrazabilidadResponse(BaseModel):
    id: UUID | None = None
    tipo_evento: str
    device_id: str | None
    usuario_id: UUID | None
    payload: dict
    timestamp: datetime
    previous_hash: str
    hash_actual: str
    # HU-24/25: cadena de la unidad monitoreada (o "SISTEMA") y posición del
    # eslabón dentro de ella.
    chain_id: str
    chain_seq: int | None = None


class DetalleInconsistenciaResponse(BaseModel):
    id: UUID
    tipo_evento: str
    timestamp: datetime
    hash_esperado: str
    hash_almacenado: str
    mensaje: str


class VerificacionIntegridadResponse(BaseModel):
    integra: bool
    total_registros: int
    primer_registro_inconsistente: int | None
    detalle_inconsistencia: DetalleInconsistenciaResponse | None = None
    registros_posteriores_afectados: int = 0


class EstadoCadenaResponse(BaseModel):
    cadena_comprometida: bool


class EstadoRegistroSegmentoResponse(BaseModel):
    id: UUID
    tipo_evento: str
    timestamp: datetime
    integro: bool


class VerificacionSegmentoResponse(BaseModel):
    """HU-37: verificación de integridad acotada a un dispositivo y periodo."""

    device_id: str
    desde: datetime
    hasta: datetime
    integra: bool
    total_bloques_verificados: int
    registros_del_dispositivo: list[EstadoRegistroSegmentoResponse]
    primer_registro_inconsistente: UUID | None = None


class ReporteBPAResponse(BaseModel):
    device_id: str | None
    fecha_desde: datetime
    fecha_hasta: datetime
    lecturas: list[LecturaResponse]
    alertas: list[AlertaResponse]
    registros_trazabilidad: list[TrazabilidadResponse]
    # RF-13: un reporte de cumplimiento que omite registros en silencio no es
    # evidencia válida. A la cadencia del firmware (30 s) el tope por colección
    # cubre ~3,5 días, así que un reporte mensual SE TRUNCA, y quien lo lee
    # tiene que saberlo.
    truncado: bool = False
    lecturas_truncadas: bool = False
    alertas_truncadas: bool = False
    trazabilidad_truncada: bool = False
    limite_por_coleccion: int | None = None


class DispositivoResponse(BaseModel):
    id: str
    nombre: str | None
    ubicacion: str | None
    estado_conectividad: str
    activo: bool
    firmware_version: str
    motivo_baja: str | None = None
    descripcion_baja: str | None = None
    dado_de_baja_en: datetime | None = None
    reemplaza_a_device_id: str | None = None
    # HU-30: estado de calibración del sensor.
    fecha_ultima_calibracion: date | None = None
    numero_certificado_calibracion: str | None = None
    fecha_proxima_calibracion: date | None = None
    observaciones_calibracion: str | None = None
    # HU-44/HU-10: estado de la credencial MQTT (nunca el token en sí).
    mqtt_token_activo: bool = False
    mqtt_credencial_actualizada_en: datetime | None = None
    # HU-43/HU-51: sensores habilitados y metadatos de instalación.
    sensores_habilitados: list[str] = Field(default_factory=list)
    fecha_instalacion: date | None = None
    instalado_por: UUID | None = None
    observaciones_instalacion: str | None = None
    # HU-53/HU-54: responsable registrado (destinatario de notificaciones).
    responsable_nombre: str | None = None
    responsable_email: str | None = None
    responsable_telefono: str | None = None


class DispositivoBajaRequest(_PeticionEstricta):
    motivo: str = Field(min_length=1, max_length=50)
    descripcion: str | None = Field(default=None, max_length=2000)
    device_id_reemplazo: str | None = Field(default=None, max_length=50)


class DispositivoAltaRequest(_PeticionEstricta):
    """HU-43: alta explícita de un dispositivo nuevo."""

    device_id: str = Field(min_length=1, max_length=50)
    nombre: str | None = Field(default=None, max_length=120)
    ubicacion: str | None = Field(default=None, max_length=200)
    sensores_habilitados: list[str] = Field(default_factory=list)


class InstalacionDispositivoRequest(_PeticionEstricta):
    """HU-51: ubicación y metadatos de instalación del sensor."""

    ubicacion: str | None = Field(default=None, max_length=200)
    fecha_instalacion: date | None = None
    observaciones: str | None = Field(default=None, max_length=2000)


class ResponsableDispositivoRequest(_PeticionEstricta):
    """HU-53/HU-54: destinatario de las notificaciones de excursión crítica
    de este dispositivo por correo y SMS."""

    nombre: str | None = Field(default=None, max_length=120)
    email: EmailStr | None = None
    telefono: str | None = Field(default=None, max_length=20)


class HistorialConfiguracionResponse(BaseModel):
    id: UUID
    device_id: str
    campo: str
    valor_anterior: str | None
    valor_nuevo: str | None
    actor_id: UUID | None
    created_at: datetime


class RotarCredencialRequest(_PeticionEstricta):
    motivo: str = Field(min_length=1, max_length=200)


class RevocarCredencialRequest(_PeticionEstricta):
    motivo: str = Field(min_length=1, max_length=200)


class CredencialDispositivoResponse(BaseModel):
    """El token en claro solo aparece aquí, en la respuesta de rotación, y
    nunca más — el backend solo conserva su hash."""

    device_id: str
    token: str
    generado_en: datetime


class AutenticarDispositivoRequest(BaseModel):
    """Sin _PeticionEstricta: la llama EMQX Cloud (webhook de autenticación
    HTTP), no un cliente de este backend con sesión."""

    device_id: str = Field(min_length=1, max_length=50)
    token: str = Field(min_length=1, max_length=200)


class AutenticarDispositivoResponse(BaseModel):
    autorizado: bool


class RegistrarVersionModeloRequest(_PeticionEstricta):
    """HU-46: metadatos de reproducibilidad de una versión entrenada."""

    version: str = Field(min_length=1, max_length=50)
    model_hash: str = Field(min_length=64, max_length=64)
    feature_schema_version: str = Field(min_length=1, max_length=20)
    dataset_version: str = Field(min_length=1, max_length=50)
    scikit_learn_version: str = Field(min_length=1, max_length=20)
    python_version: str = Field(min_length=1, max_length=20)
    trained_at: datetime


class VersionModeloResponse(BaseModel):
    id: UUID | None = None
    version: str
    model_hash: str
    feature_schema_version: str
    dataset_version: str
    scikit_learn_version: str
    python_version: str
    trained_at: datetime
    aprobado_por: UUID
    aprobado_en: datetime | None = None
    activa: bool
    activada_en: datetime | None = None
    desactivada_en: datetime | None = None


class InferenciaLecturaResponse(BaseModel):
    """HU-47: vista de solo lectura — no existe endpoint de escritura para
    este recurso, así que no hay forma de modificarlo vía la API."""

    lectura_id: UUID | None
    device_id: str
    timestamp_inferencia: datetime
    modelo_version: str | None
    vector_features: dict[str, float] | None
    probabilidades_por_clase: dict[str, float] | None
    clasificacion_final: NivelRiesgo | None
    confianza: float | None
    origen_clasificacion: str | None
    version_era_activa_y_aprobada_en_ese_momento: bool | None


class FirmwareReleaseCreateRequest(_PeticionEstricta):
    version: str = Field(min_length=1, max_length=20)
    hash_sha256: str = Field(min_length=64, max_length=64)
    descripcion: str = Field(min_length=1, max_length=2000)


class FirmwareReleaseResponse(BaseModel):
    id: UUID
    version: str
    hash_sha256: str
    descripcion: str
    fecha_compilacion: datetime


class FirmwareDespliegueCreateRequest(_PeticionEstricta):
    device_id: str = Field(min_length=1, max_length=50)
    version_objetivo: str = Field(min_length=1, max_length=20)
    programado_para: datetime | None = None


class FirmwareDespliegueResponse(BaseModel):
    id: UUID
    device_id: str
    version_objetivo: str
    estado: str
    programado_para: datetime | None
    resultado: str | None
    completado_en: datetime | None


class ChecklistBPARequest(_PeticionEstricta):
    """HU-37: los diez ítems del Manual de Buenas Prácticas de Almacenamiento.

    Todos son obligatorios: un checklist parcial no es evidencia de
    cumplimiento, así que se rechaza en el borde de la API (422) antes de
    llegar al dominio."""

    fecha: str = Field(min_length=10, max_length=10)
    temperatura: bool
    termometro: bool
    registros: bool
    alertas_revisadas: bool
    acciones_documentadas: bool
    puerta: bool
    limpieza: bool
    exclusivo: bool
    rotulado: bool
    respaldo: bool
    observaciones: str | None = Field(default=None, max_length=2000)

    @field_validator("fecha")
    @classmethod
    def _validar_formato_fecha(cls, valor: str) -> str:
        try:
            fecha = date.fromisoformat(valor)
        except ValueError as exc:
            raise ValueError("fecha debe tener formato YYYY-MM-DD") from exc
        # Un checklist no puede declararse por adelantado: se firma el día que
        # se realiza la verificación física del refrigerador.
        if fecha > datetime.now(tz=timezone.utc).date():
            raise ValueError("La fecha del checklist no puede ser futura")
        return valor


class ChecklistBPAResponse(BaseModel):
    id: UUID
    usuario_id: UUID
    fecha: str
    temperatura: bool
    termometro: bool
    registros: bool
    alertas_revisadas: bool
    acciones_documentadas: bool
    puerta: bool
    limpieza: bool
    exclusivo: bool
    rotulado: bool
    respaldo: bool
    observaciones: str | None
    total_conformes: int
    conforme: bool
    created_at: datetime
    updated_at: datetime


class CalibracionRequest(_PeticionEstricta):
    """HU-30: registro del certificado de calibración del sensor."""

    fecha_calibracion: date
    numero_certificado: str = Field(min_length=1, max_length=100)
    observaciones: str | None = Field(default=None, max_length=2000)
    # Periodicidad legal habitual del certificado; configurable por si el
    # laboratorio emite uno con vigencia distinta.
    meses_vigencia: int = Field(default=12, ge=1, le=60)

    @field_validator("fecha_calibracion")
    @classmethod
    def _no_futura(cls, valor: date) -> date:
        if valor > datetime.now(tz=timezone.utc).date():
            raise ValueError("La fecha de calibración no puede ser futura")
        return valor


class AuditLogResponse(BaseModel):
    id: UUID
    usuario_id: UUID | None
    accion: str
    recurso: str
    detalle: dict | None
    ip_origen: str | None
    created_at: datetime
