import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from src.infrastructure.database.base import Base

# JSON portable: JSONB en PostgreSQL, JSON plano en otros dialectos (usado en tests con SQLite).
JSONVariant = JSON().with_variant(JSONB(), "postgresql")
# Uuid genérico: nativo en PostgreSQL, CHAR(32) portable en otros dialectos (usado en tests con SQLite).


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _created_at_column() -> Mapped[datetime]:
    """Default generado en Python (microsegundos) en vez de solo server_default:
    CURRENT_TIMESTAMP en SQLite tiene resolución de 1 segundo y rompe el orden
    estable que necesita la verificación de la cadena de hash encadenado."""
    return mapped_column(DateTime(timezone=True), default=_utcnow, server_default=func.now())


class DeviceModel(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    nombre: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ubicacion: Mapped[str | None] = mapped_column(String(200), nullable=True)
    estado_conectividad: Mapped[str] = mapped_column(String(20), default="offline")
    created_at: Mapped[datetime] = _created_at_column()

    # HU-43: ciclo de vida (baja/reemplazo) sin borrar el histórico.
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    firmware_version: Mapped[str] = mapped_column(String(20), default="1.0.0")
    motivo_baja: Mapped[str | None] = mapped_column(String(50), nullable=True)
    descripcion_baja: Mapped[str | None] = mapped_column(Text, nullable=True)
    dado_de_baja_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reemplaza_a_device_id: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # HU-30: trazabilidad de calibración del sensor. Un registro térmico solo
    # es evidencia válida ante inspección si el instrumento que lo produjo
    # tenía certificado de calibración vigente en ese momento.
    fecha_ultima_calibracion: Mapped[date | None] = mapped_column(Date, nullable=True)
    numero_certificado_calibracion: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fecha_proxima_calibracion: Mapped[date | None] = mapped_column(Date, nullable=True)
    observaciones_calibracion: Mapped[str | None] = mapped_column(Text, nullable=True)

    # HU-44/HU-10: credencial MQTT por dispositivo. Se guarda el HASH del
    # token (nunca el valor en claro, mismo criterio que password_hasher),
    # así que ni un volcado de la base de datos revela credenciales
    # utilizables. mqtt_token_activo permite revocar de inmediato sin
    # esperar rotación: una vez False, ningún hash coincide como válido.
    mqtt_token_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mqtt_token_activo: Mapped[bool] = mapped_column(Boolean, default=False)
    mqtt_credencial_actualizada_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # HU-43: metadatos capturados en el alta explícita del dispositivo.
    sensores_habilitados: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    dado_de_alta_por: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    # HU-51: metadatos de instalación del sensor DS18B20.
    fecha_instalacion: Mapped[date | None] = mapped_column(Date, nullable=True)
    instalado_por: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    observaciones_instalacion: Mapped[str | None] = mapped_column(Text, nullable=True)

    # HU-53/HU-54: responsable registrado del dispositivo — destinatario real
    # de las notificaciones de excursión crítica por correo/SMS, en vez de un
    # único destinatario global fijo para todos los dispositivos.
    responsable_nombre: Mapped[str | None] = mapped_column(String(120), nullable=True)
    responsable_email: Mapped[str | None] = mapped_column(String(150), nullable=True)
    responsable_telefono: Mapped[str | None] = mapped_column(String(20), nullable=True)

    lecturas: Mapped[list["ThermalReadingModel"]] = relationship(back_populates="device")


class RoleModel(Base):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = _uuid_pk()
    nombre: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)

    usuarios: Mapped[list["UserModel"]] = relationship(back_populates="rol")


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(150), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    rol_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("roles.id"), nullable=False)

    # HU-44: consentimiento explícito de la Ley N.° 29733.
    privacy_accepted: Mapped[bool] = mapped_column(Boolean, default=True)
    privacy_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    privacy_version_accepted: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # HU-45: desactivación/anonimización (derecho al olvido) sin borrar audit_logs.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    motivo_desactivacion: Mapped[str | None] = mapped_column(String(50), nullable=True)
    desactivado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    desactivado_por: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    anonymized_for_gdpr: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created_at_column()

    rol: Mapped[RoleModel] = relationship(back_populates="usuarios")


class ThermalReadingModel(Base):
    __tablename__ = "thermal_readings"
    __table_args__ = (
        # Deduplicación/idempotencia (RF-07): un reenvío MQTT (PUBACK perdido,
        # QoS1) para el mismo dispositivo y el mismo instante exacto no debe
        # producir un segundo registro. Ver hallazgo B-04 de la auditoría.
        # Se conserva por compatibilidad con firmware que aún no envía
        # boot_id/seq_no; el backlog nuevo (HU-11) exige la tripleta de abajo
        # como clave de idempotencia real.
        UniqueConstraint("device_id", "timestamp", name="uq_thermal_readings_device_timestamp"),
        # HU-11/HU-05: idempotencia real por identidad lógica de la lectura.
        # Parcial (WHERE boot_id/seq_no no nulos) para no romper filas de
        # firmware anterior que aún no declara estos campos.
        Index(
            "uq_thermal_readings_device_boot_seq",
            "device_id", "boot_id", "seq_no",
            unique=True,
            sqlite_where=sa_text("boot_id IS NOT NULL AND seq_no IS NOT NULL"),
            postgresql_where=sa_text("boot_id IS NOT NULL AND seq_no IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    device_id: Mapped[str] = mapped_column(String(50), ForeignKey("devices.id"), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    temperatura_ambiental: Mapped[float | None] = mapped_column(Float, nullable=True)
    humedad_ambiental: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperatura_interna: Mapped[float | None] = mapped_column(Float, nullable=True)
    # HU-04: nullable — None cuando el dispositivo no tiene MC-38 instalado.
    apertura_refrigerador: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)
    nivel_riesgo: Mapped[str | None] = mapped_column(String(30), nullable=True)
    estado_conectividad: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    # Trazabilidad de la inferencia (RNF-04, hallazgo AI-06 de la auditoría de
    # IA): con qué versión de modelo y con qué confianza se clasificó esta
    # lectura específica. NULL cuando no se ejecutó inferencia (ORIGEN_SIN_DATO).
    modelo_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    confianza_ia: Mapped[float | None] = mapped_column(Float, nullable=True)
    origen_clasificacion: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # AIV-07 (fase de corrección): estado real de la inferencia y motivo breve
    # cuando no fue "completada". NULL en registros anteriores a esta migración.
    estado_inferencia: Mapped[str | None] = mapped_column(String(30), nullable=True)
    motivo_no_inferencia: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # HU-05: identificador lógico generado por el firmware (None en payloads
    # de dispositivos aún no actualizados) y versión del contrato de payload.
    reading_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    schema_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # HU-01/HU-11 (backlog 54 HU): identidad lógica real de la lectura.
    # boot_id incrementa en cada arranque del ESP32 (persistido en NVS);
    # seq_no es monótono dentro de ese boot. NULL en payloads de firmware
    # anterior que aún no los declara (compatibilidad, igual que reading_id).
    boot_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seq_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # HU-01: calidad de la sincronización temporal del dispositivo en el
    # instante de captura ("synced"/"unsynced"). NULL en firmware anterior.
    time_quality: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # HU-05: instante en que el backend RECIBIÓ el mensaje, distinto de
    # `timestamp` (captured_at, el instante de captura declarado por el
    # dispositivo). NULL en filas anteriores a esta columna.
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # HU-18/21/34: conceptos distintos de `nivel_riesgo` (model_class, arriba)
    # — ver docstring de LecturaTermica.calcular_riesgo_efectivo().
    excursion_confirmada: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    riesgo_efectivo: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # HU-47: evidencia completa de la inferencia (probabilidad por clase y
    # vector de features exacto), para auditoría explicativa por lectura.
    probabilidades_ia: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    vector_features_ia: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    created_at: Mapped[datetime] = _created_at_column()

    device: Mapped[DeviceModel] = relationship(back_populates="lecturas")
    alertas: Mapped[list["ThermalAlertModel"]] = relationship(
        back_populates="lectura", foreign_keys="ThermalAlertModel.reading_id"
    )


class ThermalAlertModel(Base):
    __tablename__ = "thermal_alerts"
    __table_args__ = (
        # AIV-02: garantía de "una sola alerta abierta por dispositivo y tipo
        # de riesgo" a nivel de base de datos, no solo en memoria. Un índice
        # único parcial (solo sobre filas abiertas) permite reabrir un nuevo
        # episodio del mismo tipo una vez cerrado el anterior.
        UniqueConstraint(
            "device_id", "nivel_riesgo", "episodio_abierto",
            name="uq_thermal_alerts_episodio_abierto_por_device_y_riesgo",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    reading_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("thermal_readings.id"), nullable=False)
    device_id: Mapped[str] = mapped_column(String(50), ForeignKey("devices.id"), nullable=False, index=True)
    nivel_riesgo: Mapped[str] = mapped_column(String(30), nullable=False)
    mensaje: Mapped[str] = mapped_column(Text, nullable=False)
    revisada: Mapped[bool] = mapped_column(Boolean, default=False)
    revisada_por: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True)
    # AIV-02 — control de episodio/tormenta de alertas: mientras el episodio
    # sigue abierto, esta columna vale 1 (permitiendo que el UNIQUE la
    # detecte); al cerrarse pasa a NULL (varias alertas cerradas del mismo
    # device+riesgo pueden coexistir sin violar la restricción única).
    episodio_abierto: Mapped[int | None] = mapped_column(Integer, nullable=True, default=1)
    lectura_inicial_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("thermal_readings.id"), nullable=False)
    lectura_mas_reciente_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("thermal_readings.id"), nullable=False)
    ultima_actualizacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now())
    cerrada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # HU-23: máquina de estados PENDIENTE/RECONOCIDA/ATENDIDA, además del
    # booleano revisada (que se mantiene por compatibilidad).
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="pendiente")
    reconocida_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    atendida_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created_at_column()

    lectura: Mapped[ThermalReadingModel] = relationship(back_populates="alertas", foreign_keys=[reading_id])
    acciones_correctivas: Mapped[list["CorrectiveActionModel"]] = relationship(back_populates="alerta")


class CorrectiveActionModel(Base):
    __tablename__ = "corrective_actions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    alert_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("thermal_alerts.id"), nullable=False)
    usuario_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    # HU-28: referencia a la acción que esta rectifica (None si es original).
    corrige_accion_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("corrective_actions.id"), nullable=True
    )
    created_at: Mapped[datetime] = _created_at_column()

    alerta: Mapped[ThermalAlertModel] = relationship(back_populates="acciones_correctivas")


class TraceabilityRecordModel(Base):
    __tablename__ = "traceability_records"
    __table_args__ = (
        # HU-25: chain_seq es una posición única DENTRO de su cadena, no
        # global — dos cadenas distintas reutilizan la misma numeración.
        UniqueConstraint("chain_id", "chain_seq", name="uq_traceability_chain_seq"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tipo_evento: Mapped[str] = mapped_column(String(50), nullable=False)
    device_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    usuario_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash_actual: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = _created_at_column()
    # HU-47: aislamiento de un registro corrupto y marca de "posterior al punto de ruptura".
    is_corrupted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_after_corruption: Mapped[bool] = mapped_column(Boolean, default=False)
    # HU-25: cadena independiente por unidad monitoreada (ver
    # registrar_hash_encadenado.CHAIN_ID_SISTEMA para eventos sin dispositivo)
    # y posición del eslabón dentro de ella. hash_version distingue la
    # fórmula de cálculo (1 = sin chain_id, anterior a esta migración; 2 =
    # canonical({chain_id, previous_hash, timestamp, payload})) — los
    # registros existentes NO se recalculan, ver hash_encadenado.py.
    chain_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True, server_default="SISTEMA")
    chain_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hash_version: Mapped[int] = mapped_column(Integer, nullable=False, default=2, server_default="1")


class AuditLogModel(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    usuario_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True)
    accion: Mapped[str] = mapped_column(String(100), nullable=False)
    recurso: Mapped[str] = mapped_column(String(100), nullable=False)
    detalle: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    ip_origen: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = _created_at_column()


class ReportExportModel(Base):
    __tablename__ = "report_exports"

    id: Mapped[uuid.UUID] = _uuid_pk()
    usuario_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    tipo_reporte: Mapped[str] = mapped_column(String(50), nullable=False)
    fecha_desde: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fecha_hasta: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archivo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = _created_at_column()


class FirmwareReleaseModel(Base):
    """HU-46: metadata de una versión de firmware preparada para despliegue OTA.

    Simulado a nivel de aplicación: este repositorio no contiene firmware real
    de ESP32 ni un canal MQTT de producción; no se cifra ni transmite ningún
    binario. Ver 08_hu43_47_ota_y_cierre.md para el alcance exacto."""

    __tablename__ = "firmware_releases"

    id: Mapped[uuid.UUID] = _uuid_pk()
    version: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    hash_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    fecha_compilacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _created_at_column()


class FirmwareDeploymentModel(Base):
    __tablename__ = "firmware_deployments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    device_id: Mapped[str] = mapped_column(String(50), ForeignKey("devices.id"), nullable=False)
    version_objetivo: Mapped[str] = mapped_column(String(20), nullable=False)
    estado: Mapped[str] = mapped_column(String(20), nullable=False)
    programado_para: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resultado: Mapped[str | None] = mapped_column(Text, nullable=True)
    completado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created_at_column()


class ForensicSnapshotModel(Base):
    """HU-47: snapshot forense persistido ante corrupción detectada en la cadena hash."""

    __tablename__ = "forensic_snapshots"

    id: Mapped[uuid.UUID] = _uuid_pk()
    registro_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("traceability_records.id"), nullable=True
    )
    detalle: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    created_at: Mapped[datetime] = _created_at_column()


class ChecklistBPAModel(Base):
    """HU-37: verificación diaria de Buenas Prácticas de Almacenamiento.

    UNIQUE(usuario_id, fecha): un checklist por usuario y día — volver a
    guardar el mismo día actualiza la fila, y cada guardado deja además su
    propio eslabón en `traceability_records` (la corrección queda auditable)."""

    __tablename__ = "checklist_bpa"
    __table_args__ = (
        UniqueConstraint("usuario_id", "fecha", name="uq_checklist_bpa_usuario_fecha"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    usuario_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    fecha: Mapped[str] = mapped_column(String(10), nullable=False)  # "YYYY-MM-DD"

    temperatura: Mapped[bool] = mapped_column(Boolean, nullable=False)
    termometro: Mapped[bool] = mapped_column(Boolean, nullable=False)
    registros: Mapped[bool] = mapped_column(Boolean, nullable=False)
    alertas_revisadas: Mapped[bool] = mapped_column(Boolean, nullable=False)
    acciones_documentadas: Mapped[bool] = mapped_column(Boolean, nullable=False)
    puerta: Mapped[bool] = mapped_column(Boolean, nullable=False)
    limpieza: Mapped[bool] = mapped_column(Boolean, nullable=False)
    exclusivo: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rotulado: Mapped[bool] = mapped_column(Boolean, nullable=False)
    respaldo: Mapped[bool] = mapped_column(Boolean, nullable=False)

    observaciones: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now()
    )


class SystemStateModel(Base):
    """HU-47: fila única (id=1) con el flag global cadena_comprometida."""

    __tablename__ = "system_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cadena_comprometida: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, server_default=func.now())


class ModeloIAVersionModel(Base):
    """HU-46 (backlog de 51 HU finales): registro de gobierno de versiones
    del modelo Random Forest — solo una versión REGISTRADA aquí puede
    activarse; activar una no registrada se rechaza (criterio 2 de HU-46).
    """

    __tablename__ = "ai_model_versions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    version: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    model_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_schema_version: Mapped[str] = mapped_column(String(20), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(50), nullable=False)
    scikit_learn_version: Mapped[str] = mapped_column(String(20), nullable=False)
    python_version: Mapped[str] = mapped_column(String(20), nullable=False)
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    aprobado_por: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    aprobado_en: Mapped[datetime] = _created_at_column()
    # Historial de activación: permite reconstruir, para cualquier timestamp
    # pasado, cuál era la versión activa en ese momento (HU-47 criterio 2).
    activa: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    activada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    desactivada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeviceConfigHistoryModel(Base):
    """HU-49: historial auditable de cambios de configuración/instalación/
    calibración/reemplazo del hardware — valor anterior, valor nuevo, actor
    y timestamp por cada cambio de campo. La telemetría y sus hashes previos
    nunca se recalculan cuando cambia una fila de este historial."""

    __tablename__ = "device_config_history"

    id: Mapped[uuid.UUID] = _uuid_pk()
    device_id: Mapped[str] = mapped_column(String(50), ForeignKey("devices.id"), nullable=False, index=True)
    campo: Mapped[str] = mapped_column(String(50), nullable=False)
    valor_anterior: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor_nuevo: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = _created_at_column()
