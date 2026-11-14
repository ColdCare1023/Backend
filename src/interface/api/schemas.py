from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    AliasChoices,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
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
    # Nulo solo cuando `requiere_codigo` es true: la sesión se abre después,
    # en POST /api/auth/login/verificar.
    access_token: str | None = None
    token_type: str = "bearer"
    require_privacy_consent: bool = False
    # BE-025: contraseña temporal; el frontend debe llevar al cambio.
    require_password_change: bool = False
    # Segundo factor por correo (OTP_LOGIN_ENABLED). El frontend pide el código
    # y lo envía junto con `desafio` (opaco, de un solo uso).
    requiere_codigo: bool = False
    desafio: str | None = None
    email_enmascarado: str | None = None
    expira_en_segundos: int | None = None
    reenviar_en_segundos: int | None = None


# Código de verificación: 6 dígitos; se aceptan espacios o guiones al pegarlo.
CodigoVerificacionTexto = Annotated[str, StringConstraints(min_length=6, max_length=12, pattern=r"^[0-9\s-]+$")]
DesafioTexto = Annotated[str, StringConstraints(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


class VerificarCodigoLoginRequest(_PeticionEstricta):
    desafio: DesafioTexto
    codigo: CodigoVerificacionTexto


class ReenviarCodigoLoginRequest(_PeticionEstricta):
    desafio: DesafioTexto


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
    # Celular E.164 (+51999888777): recibe el SMS de bienvenida al crear la cuenta.
    telefono: str | None = Field(default=None, pattern=r"^\+[1-9]\d{7,14}$")


class CambiarPasswordRequest(_PeticionEstricta):
    password_actual: str = Field(min_length=1, max_length=256)
    password_nueva: PasswordNueva


class RecuperarPasswordRequest(_PeticionEstricta):
    email: EmailStr


class RestablecerPasswordRequest(_PeticionEstricta):
    """Dos formas, excluyentes:

    - legado (BE-025): `{token, password_nueva}` con el token del enlace;
    - por código: `{email, codigo, password_nueva}` con el código de
      recuperación o de activación recibido por correo.

    `nueva_password` se acepta como sinónimo de `password_nueva`."""

    token: str | None = Field(default=None, min_length=20, max_length=256)
    email: EmailStr | None = None
    codigo: CodigoVerificacionTexto | None = None
    password_nueva: PasswordNueva = Field(validation_alias=AliasChoices("password_nueva", "nueva_password"))

    @model_validator(mode="after")
    def _token_o_codigo(self) -> "RestablecerPasswordRequest":
        por_codigo = self.email is not None or self.codigo is not None
        if self.token is not None and por_codigo:
            raise ValueError("Envíe el token del enlace o el correo con el código, no ambos")
        if self.token is None and not (self.email is not None and self.codigo is not None):
            raise ValueError("Falta el token del enlace o el correo y el código de verificación")
        return self


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
    # Enmascarado (+51••••••777): el número completo no sale por la API.
    telefono: str | None = None


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
    # HU-35: `duracion_apertura_segundos` solo cuenta dentro de la ventana de
    # muestreo (nunca supera el intervalo). Esto es cuánto lleva abierta la
    # puerta SIN cerrarse al capturar; None si estaba cerrada o el firmware no
    # lo reporta.
    apertura_continua_segundos: int | None = None
    # HU-35: umbral configurado (PUERTA_UMBRAL_PROLONGADA_SEGUNDOS) a partir
    # del cual la apertura en curso se destaca como prolongada.
    umbral_puerta_abierta_segundos: int | None = None
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
    # HU-20: la clase la produjo el modelo legado (datos sintéticos) en modo
    # experimental. El tablero la rotula y no abre alertas preventivas por ella.
    ia_experimental: bool = False
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
    # Valores internos (contrato_v1.TIME_QUALITY_INTERNO): el OpenAPI los publica
    # como enum para que el frontend los valide en sus pruebas de contrato.
    time_quality: Literal["synced", "backfilled", "unsynced"] | None = None
    timestamp_estimado: datetime | None = None
    # BE-001: "dispositivo" (telemetría del nodo) o "manual" (registrada por
    # `registrado_por`). Un reporte o una pantalla no deben mezclarlas.
    origen: str = "dispositivo"
    registrado_por: UUID | None = None
    # HU-07 (H2): lectura de un arranque sin hora sincronizada que aún no se
    # pudo reanclar. Su `timestamp` es la hora de LLEGADA al backend, no la de
    # captura: la interfaz debe rotularla «sin hora» y no ubicarla en la curva.
    sin_hora: bool = False
    # De dónde sale la hora que se muestra: "nodo" (reloj sincronizado o
    # fechada por el nodo), "reconstruida" (`timestamp_estimado`),
    # "recepcion" (sin hora: llegada al backend) o "manual" (la declaró quien
    # registró la lectura).
    origen_hora: Literal["nodo", "reconstruida", "recepcion", "manual"] = "nodo"
    # HU-05: instante en que el backend recibió la lectura (None en filas antiguas).
    recibida_en: datetime | None = None


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
    # Aviso por correo: `notificada_en` se llena solo con un envío exitoso;
    # `notificacion_intentos` cuenta los intentos (fallidos incluidos).
    notificada_en: datetime | None = None
    notificacion_intentos: int = 0
    # Evidencia de la excursión: inicio (hora de captura), pico, duración y si
    # se detectó con lecturas diferidas del buffer del nodo.
    iniciada_en: datetime | None = None
    temperatura_pico: float | None = None
    duracion_s: float | None = None
    historica: bool = False


class AccionCorrectivaCreateRequest(_PeticionEstricta):
    """HU-27: decisión adoptada, acción ejecutada (`descripcion`) y justificación."""

    decision: str = Field(min_length=1, max_length=2000)
    descripcion: str = Field(min_length=1, max_length=2000)
    justificacion: str = Field(min_length=1, max_length=2000)


class AccionCorrectivaRectificarRequest(_PeticionEstricta):
    """HU-28 esc. 2: corrige la decisión, la acción ejecutada (`descripcion`)
    y/o la justificación de la acción vigente, con su motivo (P6). Lo que no se
    envía se hereda de la versión corregida; al menos un campo debe venir."""

    descripcion: str | None = Field(default=None, min_length=1, max_length=2000)
    decision: str | None = Field(default=None, min_length=1, max_length=2000)
    justificacion: str | None = Field(default=None, min_length=1, max_length=2000)
    motivo_rectificacion: str = Field(min_length=10, max_length=500)

    @model_validator(mode="after")
    def _algo_que_corregir(self) -> "AccionCorrectivaRectificarRequest":
        if self.descripcion is None and self.decision is None and self.justificacion is None:
            raise ValueError("Indique la decisión, la acción o la justificación que se corrige")
        return self


class AccionCorrectivaResponse(BaseModel):
    id: UUID | None = None
    alert_id: UUID
    usuario_id: UUID
    # D7: nombre ya resuelto ("Usuario desactivado" si fue anonimizado, HU-45).
    usuario_nombre: str | None = None
    decision: str | None = None
    descripcion: str
    justificacion: str | None = None
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
    # HU-26 escenario 2: dónde y de qué tipo es cada discrepancia.
    chain_id: str | None = None
    indice: int | None = None
    tipo_discrepancia: Literal["payload_alterado", "enlace_roto", "no_verificable"] | None = None
    aislada: bool = False


class VerificacionIntegridadResponse(BaseModel):
    integra: bool
    total_registros: int
    primer_registro_inconsistente: int | None
    detalle_inconsistencia: DetalleInconsistenciaResponse | None = None
    registros_posteriores_afectados: int = 0
    # HU-26: la verificación no se detiene en la primera ruptura.
    # `no_concluida` (HU-26 esc. 1): hay eslabones v3 que no se pudieron
    # verificar sin la clave HMAC y ninguna ruptura demostrada; no es íntegra.
    estado: Literal["integra", "ruptura_aislada", "ruptura_detectada", "no_concluida"] = "integra"
    total_rupturas: int = 0
    rupturas: list[DetalleInconsistenciaResponse] = Field(default_factory=list)
    registros_posteriores_consistentes: int = 0
    integra_desde: datetime | None = None
    # HU-26 escenario 4: alcance explícito del resultado (no es certificación
    # DIGEMID), redactado según las versiones de hash verificadas.
    alcance: str
    # Cadena verificada (None = todas) y versiones de hash encontradas en ella.
    chain_id: str | None = None
    versiones_hash: list[int] = Field(default_factory=list)
    # Eslabones que no se pudieron verificar (sin clave HMAC); no son rupturas.
    total_no_verificables: int = 0
    no_verificables: list[DetalleInconsistenciaResponse] = Field(default_factory=list)
    # HU-38: hash_actual completo (64 hex) del último eslabón examinado.
    hash_final: str | None = None


class CadenaTrazabilidadResponse(BaseModel):
    """HU-26: cadenas existentes (una por dispositivo y SISTEMA) para elegir
    cuál verificar."""

    chain_id: str
    es_sistema: bool
    total_registros: int
    ultimo_registro_en: datetime | None = None


class EstadoCadenaResponse(BaseModel):
    cadena_comprometida: bool


class EstadoRegistroSegmentoResponse(BaseModel):
    id: UUID
    tipo_evento: str
    timestamp: datetime
    integro: bool
    # HU-37 esc. 2: el registro alterado se rotula "corrupto" con su ID y
    # timestamp; sin clave HMAC, "no_verificable" (no se da por íntegro).
    estado: Literal["integro", "corrupto", "no_verificable"] = "integro"
    tipo_discrepancia: Literal["payload_alterado", "enlace_roto", "no_verificable"] | None = None
    chain_seq: int | None = None


class VerificacionSegmentoResponse(BaseModel):
    """HU-37: verificación de integridad acotada a un dispositivo y periodo."""

    device_id: str
    desde: datetime
    hasta: datetime
    integra: bool
    total_bloques_verificados: int
    registros_del_dispositivo: list[EstadoRegistroSegmentoResponse]
    primer_registro_inconsistente: UUID | None = None
    alcance: str
    # Resultado global: `integra` sigue siendo el booleano de siempre.
    estado: Literal["integra", "corrupta", "no_concluida"] = "integra"
    total_corruptos: int = 0
    total_no_verificables: int = 0
    versiones_hash: list[int] = Field(default_factory=list)
    # Hash del eslabón inmediatamente anterior al periodo (o génesis): el ancla.
    hash_ancla: str | None = None


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
    margen_aviso_calibracion_dias: int = 30
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
    # HU-43/HU-44: operación en el broker sin confirmar («aprovisionar» o
    # «retirar»). Mientras no sea None, la consola de EMQX no refleja el estado
    # de la credencial que muestra el sistema.
    broker_pendiente: Literal["aprovisionar", "retirar"] | None = None
    broker_pendiente_motivo: str | None = None
    broker_pendiente_desde: datetime | None = None
    broker_pendiente_por: UUID | None = None
    broker_confirmado_en: datetime | None = None
    broker_confirmado_por: UUID | None = None
    # HU-44: rotación por compromiso en curso: la telemetría se retiene.
    rotacion_por_compromiso: bool = False
    # HU-44: consola confirmada; la retención sigue hasta que el nodo pruebe
    # (desafío firmado) que tiene el token nuevo.
    posesion_pendiente: bool = False
    # HU-54: intervalo de captura del nodo y último latido (`estado_nodo`).
    intervalo_reporte_segundos: int = 30
    ultimo_latido_en: datetime | None = None


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
    margen_aviso_calibracion_dias: int = 30
    sensores_habilitados: list[str] = Field(default_factory=list)


class SensorIoTResponse(BaseModel):
    modelo: Literal["DS18B20", "SHT31", "MC-38"]
    estado: Literal["ok", "error", "no_instalado"]
    campo_telemetria: str


class IncidenteOperativoResponse(BaseModel):
    """Incidente operativo, separado del riesgo térmico y de la IA: sin datos
    (HU-54) y, durante una rotación por compromiso (HU-44), monitoreo suspendido
    o excursión crítica de origen no verificado."""

    id: str
    device_id: str
    tipo: Literal["sin_datos", "monitoreo_suspendido", "excursion_no_verificada", "sensor_fault", "colision_clave"]
    estado: Literal["activo", "resuelto"]
    detectado_en: datetime
    ultimo_reporte_en: datetime | None
    umbral_silencio_segundos: int
    recuperado_en: datetime | None = None
    # HU-54: cuándo salió el aviso (correo) del incidente persistente.
    notificado_en: datetime | None = None
    # HU-44: lectura que disparó la excursión no verificada.
    detalle: dict | None = None


class BrechaBufferResponse(BaseModel):
    """HU-48 esc. 2: tramo en que LittleFS estuvo lleno y el nodo no pudo
    almacenar muestras nuevas. `desde`/`hasta` son None si el nodo no tenía
    hora al perderlas (se identifican por `*_boot_id`/`*_seq_no`)."""

    desde: datetime | None
    hasta: datetime | None
    # Mismo valor en los dos nombres: `muestras_perdidas` es el que ya lee el
    # tablero; `perdidas`, el del evento del nodo.
    muestras_perdidas: int
    perdidas: int
    causa: Literal["buffer_agotado"] = "buffer_agotado"
    # Causa tal como la reporta el firmware (p. ej. "buffer_lleno").
    causa_nodo: str
    descripcion: str
    abierta: bool
    registrada_en: datetime
    actualizada_en: datetime
    cerrada_en: datetime | None = None
    desde_boot_id: int | None = None
    desde_seq_no: int | None = None
    hasta_boot_id: int | None = None
    hasta_seq_no: int | None = None


class BufferLittleFSResponse(BaseModel):
    """HU-48: datos pendientes de sincronizar en el buffer LittleFS del nodo
    (último `estado_nodo`) y brechas por capacidad agotada.

    `desde`/`hasta`: rango temporal de los pendientes (None si no hay). Si el
    firmware no lo informa se estima (`rango_estimado = true`): desde la
    última lectura persistida del nodo hasta su último latido."""

    pendientes: int
    limite: int
    ocupacion_pct: int | None = None
    desde: datetime | None = None
    hasta: datetime | None = None
    rango_estimado: bool = False
    # Muestras no almacenadas desde la instalación (contador persistente del nodo).
    perdidas: int
    cuarentena: int | None = None
    # Cuándo llegó el último `estado_nodo` (None si solo hay brechas).
    reportado_en: datetime | None = None
    # Último latido con 0 pendientes (HU-48 esc. 3).
    ultima_sincronizacion_en: datetime | None = None
    brechas: list[BrechaBufferResponse] = Field(default_factory=list)


class EstadoOperativoResponse(DispositivoResumenResponse):
    """HU-33: estado operativo del nodo a partir de su última lectura recibida.

    Es independiente de la clasificación térmica: un nodo puede estar
    OPERATIVO con la temperatura fuera de rango, o en FALLA_SENSOR sin que eso
    sea una excursión.
    """

    ultimo_reporte_en: datetime | None = None
    intervalo_reporte_segundos: int
    umbral_sin_datos_segundos: int
    estado_operativo: Literal["OPERATIVO", "SIN_DATOS", "FALLA_SENSOR", "MONITOREO_SUSPENDIDO"]
    sensores: list[SensorIoTResponse] = Field(default_factory=list)
    incidencias_operativas: list[IncidenteOperativoResponse] = Field(default_factory=list)
    # HU-35: umbral configurado (PUERTA_UMBRAL_PROLONGADA_SEGUNDOS) a partir del
    # cual una apertura en curso se destaca como prolongada. Se informa aunque
    # aún no haya lecturas, para que el tablero no use un valor propio.
    umbral_puerta_abierta_segundos: int | None = None
    # HU-48: None si el nodo nunca informó su buffer (firmware sin `estado_nodo`).
    buffer_littlefs: BufferLittleFSResponse | None = None


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
    # HU-49 esc. 2: campo sin el origen ("intervalo_reporte_segundos"), quién
    # lo originó ("administrador"/"nodo") si consta, grupo para filtrar y
    # desde/hasta cuándo estuvo vigente (None = sigue vigente).
    campo_base: str | None = None
    origen: str | None = None
    categoria: str | None = None
    vigente_desde: datetime | None = None
    vigente_hasta: datetime | None = None
    actor_nombre: str | None = None


class RotarCredencialRequest(_PeticionEstricta):
    motivo: str = Field(min_length=1, max_length=200)
    # HU-44: «compromiso» = el token anterior se filtró. Si la consola del broker
    # queda pendiente, la telemetría del nodo se retiene hasta confirmarla.
    causa: Literal["preventiva", "compromiso"] = "preventiva"


class RevocarCredencialRequest(_PeticionEstricta):
    motivo: str = Field(min_length=1, max_length=200)


class CredencialDispositivoResponse(BaseModel):
    """El token en claro solo aparece aquí, en la respuesta de rotación, y
    nunca más — el backend solo conserva su hash."""

    device_id: str
    token: str
    generado_en: datetime
    # BE-002: True si el token quedó aprovisionado en la autenticación
    # integrada del broker; False si el broker lo rechazó; None si la API del
    # broker no está configurada (el token no sirve hasta aprovisionarlo).
    credencial_aprovisionada: bool | None = None
    # Instrucción cuando el aprovisionamiento no fue automático (modo manual,
    # p. ej. EMQX Serverless, o API sin configurar).
    mensaje_broker: str | None = None
    # BE-002: True si el broker cerró la sesión anterior; None si la API del
    # broker no está configurada.
    sesion_broker_cerrada: bool | None = None
    # HU-44: rotación por compromiso con la consola pendiente: la ingesta
    # retiene la telemetría (sin acuse) hasta que se confirme la consola.
    telemetria_retenida: bool = False


class DispositivoAltaResponse(DispositivoResponse):
    """HU-43: el alta emite la credencial MQTT del nodo. El token en claro
    solo aparece en esta respuesta (igual que en la rotación)."""

    credencial: CredencialDispositivoResponse


class ConfirmarBrokerRequest(_PeticionEstricta):
    """HU-43/HU-44: el administrador confirma que hizo la operación pendiente
    en la consola del broker."""

    nota: str | None = Field(default=None, max_length=500)


class IntervaloReporteRequest(_PeticionEstricta):
    """HU-54: intervalo de captura del nodo, en segundos."""

    segundos: int = Field(ge=5, le=3600)


class RegistrarVersionModeloRequest(_PeticionEstricta):
    """HU-46: metadatos de reproducibilidad de una versión entrenada."""

    version: str = Field(min_length=1, max_length=50)
    model_hash: str = Field(min_length=64, max_length=64)
    feature_schema_version: str = Field(min_length=1, max_length=20)
    dataset_version: str = Field(min_length=1, max_length=50)
    scikit_learn_version: str = Field(min_length=1, max_length=20)
    python_version: str = Field(min_length=1, max_length=20)
    trained_at: datetime
    # HU-46: evidencia de evaluación. Opcional al registrar; obligatoria para activar.
    horizonte_min: int | None = Field(default=None, ge=1, le=24 * 60)
    metricas: dict[str, float | int] | None = None
    particiones: dict[str, str | int | float | list[str]] | None = None


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
    horizonte_min: int | None = None
    metricas: dict | None = None
    particiones: dict | None = None
    # HU-46: ambos juegos de criterios y lo que impide activar la versión.
    evaluacion_criterios: dict | None = None
    criterio_principal: str | None = None
    activable: bool = False
    motivos_bloqueo: list[str] = Field(default_factory=list)


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
    # B-13: lo que el panel de evidencia muestra junto a la clase del modelo.
    estado_inferencia: str | None = None
    excursion_confirmada: bool = False
    riesgo_efectivo: NivelRiesgo | None = None
    # HU-18 esc. 4: contexto de la inferencia.
    horizonte_min: int | None = None
    feature_schema_version: str | None = None
    politica_operativa: str | None = None
    # HU-20: la clase la produjo el modelo legado (datos sintéticos).
    ia_experimental: bool = False


# IOT-004: tamaño de la partición de aplicación del nodo (1280 KB, IoT ADR-18).
OTA_TAMANO_MAXIMO_BYTES = 0x140000


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
    # HU-30: la vigencia sale del certificado. `meses_vigencia` queda para
    # clientes anteriores; sin ninguno de los dos no se impone un periodo.
    fecha_vencimiento: date | None = None
    meses_vigencia: int | None = Field(default=None, ge=1, le=60)
    margen_aviso_dias: int = Field(default=30, ge=1, le=365)

    @model_validator(mode="after")
    def _vigencia_indicada(self) -> "CalibracionRequest":
        if self.fecha_vencimiento is None and self.meses_vigencia is None:
            raise ValueError("Indique la fecha de vencimiento del certificado")
        if self.fecha_vencimiento is not None and self.fecha_vencimiento <= self.fecha_calibracion:
            raise ValueError("El vencimiento debe ser posterior a la fecha de calibración")
        return self

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
    # HU-42 esc. 1 / HU-50: resultado de la operación. En filas anteriores a la
    # migración 0039 se deriva de la acción (la bitácora no se reescribe).
    resultado: Literal["exitoso", "fallido", "denegado", "bloqueado"] = "exitoso"


class EstadisticaMagnitudResponse(BaseModel):
    """Mínimo, máximo y promedio de una magnitud; `n` cuenta solo lecturas con valor."""

    min: float | None = None
    max: float | None = None
    promedio: float | None = None
    n: int = 0


class BucketSerieResponse(BaseModel):
    inicio: datetime
    fin: datetime
    lecturas: int
    temperatura_interna: EstadisticaMagnitudResponse
    temperatura_ambiental: EstadisticaMagnitudResponse
    humedad_ambiental: EstadisticaMagnitudResponse
    # Lecturas con temperatura interna fuera de 2-8 °C y con excursión confirmada.
    fuera_de_rango: int = 0
    excursiones: int = 0


class BrechaSerieResponse(BaseModel):
    """Tramo sin telemetría mayor que el umbral de silencio del nodo."""

    desde: datetime
    hasta: datetime
    duracion_segundos: int
    # "buffer_agotado" si coincide con un episodio de buffer saturado (HU-48).
    causa: Literal["sin_datos", "buffer_agotado"]
    perdidas: int | None = None
    # La brecha llega hasta ahora: el nodo sigue sin reportar.
    abierta: bool = False


class ResumenSerieResponse(BaseModel):
    lecturas: int
    temperatura_interna: EstadisticaMagnitudResponse
    temperatura_ambiental: EstadisticaMagnitudResponse
    humedad_ambiental: EstadisticaMagnitudResponse
    fuera_de_rango: int = 0
    excursiones: int = 0
    por_riesgo_efectivo: dict[str, int] = Field(default_factory=dict)
    primera_lectura_en: datetime | None = None
    ultima_lectura_en: datetime | None = None


class SerieTermicaResponse(BaseModel):
    """HU-31 / HU-36: serie agregada del periodo completo, sin tope de filas.

    Cada intervalo (`bucket_segundos`, alineado a la época UNIX) trae mínimo,
    máximo, promedio y conteo; los intervalos sin lecturas no se incluyen y
    los tramos sin datos se listan en `brechas`. `total_lecturas` es el número
    de lecturas del nodo con hora en el periodo (la suma de `lecturas` de los
    intervalos): la interfaz lo compara con lo que trajo `/api/lecturas` para
    avisar de un recorte. Las lecturas sin hora (HU-07) y las manuales no
    entran en la curva y se cuentan aparte."""

    device_id: str
    desde: datetime
    hasta: datetime
    bucket_segundos: int
    bucket_automatico: bool
    intervalo_reporte_segundos: int
    umbral_brecha_segundos: int
    total_lecturas: int
    lecturas_sin_hora: int
    lecturas_manuales: int
    buckets: list[BucketSerieResponse]
    brechas: list[BrechaSerieResponse]
    brechas_truncadas: bool = False
    resumen: ResumenSerieResponse
