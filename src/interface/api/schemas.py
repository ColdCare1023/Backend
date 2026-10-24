from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
)

from src.domain.entities.checklist_bpa import DIAS_RETROACTIVOS_MAXIMOS
from src.domain.value_objects.device_id import PATRON_DEVICE_ID
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.domain.value_objects.rol import Rol

PASSWORD_MIN_LENGTH = 10

# BE-017: todo device_id que entra por la API cumple el patrón del contrato.
DeviceId = Annotated[str, StringConstraints(pattern=PATRON_DEVICE_ID)]

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
    # BE-025: contraseña temporal; el frontend debe llevar al cambio.
    require_password_change: bool = False


class SSETicketResponse(BaseModel):
    ticket: str


class PrivacidadResponse(BaseModel):
    privacy_accepted: bool
    privacy_version_accepted: str | None


def _politica_password(valor: str) -> str:
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


# BE-025: la misma política en el alta, el cambio y el restablecimiento.
PasswordNueva = Annotated[str, Field(min_length=PASSWORD_MIN_LENGTH), AfterValidator(_politica_password)]


class UsuarioCreateRequest(_PeticionEstricta):
    nombre: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: PasswordNueva
    rol: Rol


class CambiarPasswordRequest(_PeticionEstricta):
    password_actual: str = Field(min_length=1, max_length=256)
    password_nueva: PasswordNueva


class RecuperarPasswordRequest(_PeticionEstricta):
    email: EmailStr


class RestablecerPasswordRequest(_PeticionEstricta):
    token: str = Field(min_length=20, max_length=256)
    password_nueva: PasswordNueva


class SolicitudRecuperacionResponse(BaseModel):
    detail: str


class UsuarioResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nombre: str
    email: str
    rol: Rol
    is_active: bool = True
    motivo_desactivacion: str | None = None
    desactivado_en: datetime | None = None
    debe_cambiar_password: bool = False


class PasswordTemporalResponse(BaseModel):
    """BE-025: la contraseña temporal se muestra una sola vez, en esta
    respuesta (no se guarda en claro en ninguna parte)."""

    usuario: UsuarioResponse
    password_temporal: str


class DesactivarUsuarioRequest(_PeticionEstricta):
    motivo: str = Field(min_length=1, max_length=50)


class CambiarRolUsuarioRequest(_PeticionEstricta):
    """HU-41: petición para asignar/modificar el rol de un usuario existente."""

    rol: Rol


class LecturaIngestRequest(_PeticionEstricta):
    """BE-001: lectura registrada manualmente por una persona.

    La telemetría de los dispositivos entra solo por MQTT, autenticada ante el
    broker. Por esta vía no se aceptan identificadores lógicos del nodo
    (`boot_id`, `seq_no`, `reading_id`), ni versión de firmware o de contrato:
    los asigna el servidor y la lectura queda marcada como `origen="manual"`
    con su autor, separada de la evidencia del dispositivo."""

    device_id: DeviceId
    timestamp: datetime
    temperatura_ambiental: float | None = Field(default=None, ge=-40.0, le=125.0)
    humedad_ambiental: float | None = Field(default=None, ge=0.0, le=100.0)
    temperatura_interna: float | None = Field(default=None, ge=-55.0, le=125.0)
    apertura_refrigerador: bool = False
    estado_conectividad: Literal["online", "offline"] = "online"
    duracion_apertura_segundos: int = Field(default=0, ge=0)


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
    # HU-01/HU-06: calidad del reloj del nodo al capturar y, para lecturas
    # `unsynced`, la hora reconstruida (None hasta que el arranque sincroniza).
    # Para graficar, usar `timestamp_estimado` cuando exista.
    time_quality: str | None = None
    timestamp_estimado: datetime | None = None
    # BE-001: "dispositivo" (telemetría del nodo) o "manual" (registrada por
    # `registrado_por`). Un reporte o una pantalla no deben mezclarlas.
    origen: str = "dispositivo"
    registrado_por: UUID | None = None


class AlertaResponse(BaseModel):
    id: UUID | None = None
    reading_id: UUID
    device_id: str
    nivel_riesgo: NivelRiesgo
    mensaje: str
    revisada: bool
    revisada_por: UUID | None = None
    # D7: quién reconoció la alerta, ya resuelto y anonimizado si corresponde.
    revisada_por_nombre: str | None = None
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
    """HU-28: nueva justificación que corrige la anterior, con su motivo (P6)."""

    descripcion: str = Field(min_length=1, max_length=2000)
    motivo_rectificacion: str = Field(min_length=10, max_length=500)


class AccionCorrectivaResponse(BaseModel):
    id: UUID | None = None
    alert_id: UUID
    usuario_id: UUID
    # D7: nombre ya resuelto ("Usuario desactivado" si fue anonimizado, HU-45).
    usuario_nombre: str | None = None
    descripcion: str
    created_at: datetime | None = None
    corrige_accion_id: UUID | None = None
    # HU-28: motivo de la rectificación, qué acción la rectificó a ella y si es
    # la versión vigente (la única que puede volver a rectificarse).
    motivo_rectificacion: str | None = None
    rectificada_por_id: UUID | None = None
    vigente: bool = True


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
    # HU-13: desde cuándo rige `estado_conectividad` («Desconectado desde…»).
    conectividad_cambiada_en: datetime | None = None
    activo: bool
    # BE-015: dispositivo de demostración (admite lecturas `simulated`).
    es_demo: bool = False
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


class DispositivoResumenResponse(BaseModel):
    """BE-013: vista de un dispositivo para roles que no lo administran.

    Sin datos de contacto del responsable, sin estado de credenciales ni
    metadatos de instalación: solo lo que necesitan el estado operativo y el
    aviso de calibración. La vista completa (`DispositivoResponse`) queda para
    la administración de dispositivos."""

    id: str
    nombre: str | None
    ubicacion: str | None
    estado_conectividad: str
    # HU-13: el badge de conectividad muestra «Desconectado desde…».
    conectividad_cambiada_en: datetime | None = None
    activo: bool
    firmware_version: str
    fecha_ultima_calibracion: date | None = None
    numero_certificado_calibracion: str | None = None
    fecha_proxima_calibracion: date | None = None
    sensores_habilitados: list[str] = Field(default_factory=list)


class SensorIoTResponse(BaseModel):
    modelo: Literal["DS18B20", "SHT31", "MC-38"]
    estado: Literal["ok", "error", "no_instalado"]
    campo_telemetria: str


class IncidenteOperativoResponse(BaseModel):
    """Incidente operativo (sin datos), separado del riesgo térmico y de la IA."""

    id: str
    device_id: str
    tipo: Literal["sin_datos"]
    estado: Literal["activo", "resuelto"]
    detectado_en: datetime
    ultimo_reporte_en: datetime | None
    umbral_silencio_segundos: int
    recuperado_en: datetime | None = None


class EstadoOperativoResponse(DispositivoResumenResponse):
    """HU-33: estado operativo del nodo a partir de su última lectura recibida.

    Es independiente de la clasificación térmica: un nodo puede estar
    OPERATIVO con la temperatura fuera de rango, o en FALLA_SENSOR sin que eso
    sea una excursión.
    """

    ultimo_reporte_en: datetime | None = None
    intervalo_reporte_segundos: int
    umbral_sin_datos_segundos: int
    estado_operativo: Literal["OPERATIVO", "SIN_DATOS", "FALLA_SENSOR"]
    sensores: list[SensorIoTResponse] = Field(default_factory=list)
    incidencias_operativas: list[IncidenteOperativoResponse] = Field(default_factory=list)


class DispositivoBajaRequest(_PeticionEstricta):
    motivo: str = Field(min_length=1, max_length=50)
    descripcion: str | None = Field(default=None, max_length=2000)
    device_id_reemplazo: DeviceId | None = None


class DispositivoAltaRequest(_PeticionEstricta):
    """HU-43: alta explícita de un dispositivo nuevo."""

    device_id: DeviceId
    nombre: str | None = Field(default=None, max_length=120)
    ubicacion: str | None = Field(default=None, max_length=200)
    sensores_habilitados: list[str] = Field(default_factory=list)
    # BE-015: solo para el nodo de la demostración; nunca un equipo del local.
    es_demo: bool = False


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
    # BE-002: True si el broker cerró la sesión anterior; None si la API del
    # broker no está configurada (la credencial vieja muere al reconectar).
    sesion_broker_cerrada: bool | None = None


class AutenticarDispositivoRequest(BaseModel):
    """Sin _PeticionEstricta: la llama EMQX Cloud (webhook de autenticación
    HTTP), no un cliente de este backend con sesión."""

    device_id: DeviceId
    token: str = Field(min_length=1, max_length=200)
    # IOT-006: CN del certificado de cliente verificado por el broker
    # (placeholder `${cert_common_name}` de EMQX). Sin certificado, EMQX lo
    # envía vacío o como "undefined".
    cert_common_name: str | None = Field(default=None, max_length=256)

    @field_validator("cert_common_name")
    @classmethod
    def _sin_certificado_es_none(cls, valor: str | None) -> str | None:
        if valor is None or valor.strip() in ("", "undefined", "null"):
            return None
        return valor


class AutenticarDispositivoResponse(BaseModel):
    """BE-002: formato del autenticador HTTP de EMQX (`result`,
    `is_superuser`, `expire_at` opcional). `autorizado` se conserva por
    compatibilidad con clientes anteriores."""

    result: Literal["allow", "deny"]
    is_superuser: bool = False
    expire_at: int | None = None
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


# IOT-004: tamaño de la partición de aplicación del nodo (1216 KB).
OTA_TAMANO_MAXIMO_BYTES = 0x130000


class FirmwareReleaseCreateRequest(_PeticionEstricta):
    version: str = Field(min_length=1, max_length=20)
    hash_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    descripcion: str = Field(min_length=1, max_length=2000)
    # IOT-004: con ambos, la release se instala por OTA real (orden MQTT al nodo).
    url_descarga: str | None = Field(default=None, pattern=r"^https://[^\s\"\\]{1,248}$")
    tamano_bytes: int | None = Field(default=None, ge=1, le=OTA_TAMANO_MAXIMO_BYTES)


class FirmwareReleaseResponse(BaseModel):
    id: UUID
    version: str
    hash_sha256: str
    descripcion: str
    fecha_compilacion: datetime
    url_descarga: str | None = None
    tamano_bytes: int | None = None


class FirmwareDespliegueCreateRequest(_PeticionEstricta):
    device_id: DeviceId
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
        hoy = datetime.now(tz=timezone.utc).date()
        if fecha > hoy:
            raise ValueError("La fecha del checklist no puede ser futura")
        if fecha < hoy - timedelta(days=DIAS_RETROACTIVOS_MAXIMOS):
            raise ValueError(
                "El checklist solo puede registrarse para hoy o el día anterior: "
                "una declaración retroactiva no es evidencia de la verificación física"
            )
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
