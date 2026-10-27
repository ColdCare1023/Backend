import hashlib
import warnings
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SECRETO_POR_DEFECTO = "clave_secreta_larga_y_aleatoria_cambiar_en_produccion"

# BE-023: secretos que quedaron publicados en el historial del repositorio
# (constante `JWT_SECRET` de `src/auth.py`, commits 7ad2696..ab2174a). Se
# guarda su SHA-256, nunca el valor: cualquiera con acceso al historial lo
# conoce, así que no puede usarse como secreto en producción.
_HASHES_SECRETOS_PUBLICADOS = frozenset(
    {"a178e9bd86ba88b91b7cc9ecbec9297f41ebb34c5b7bea22c5e2cef2158a2d23"}
)


def _secreto_publicado(valor: str) -> bool:
    return hashlib.sha256(valor.encode()).hexdigest() in _HASHES_SECRETOS_PUBLICADOS


_PREFIJOS_POSTGRES_SIN_DRIVER = ("postgres://", "postgresql://")


def normalizar_database_url(url: str) -> str:
    for prefijo in _PREFIJOS_POSTGRES_SIN_DRIVER:
        if url.startswith(prefijo):
            url = "postgresql+asyncpg://" + url[len(prefijo):]
            break
    if url.startswith("postgresql+asyncpg://"):
        url = url.replace("sslmode=", "ssl=")
    return url

# RFC 7518 §3.2: la clave HMAC de HS256 debe tener al menos el tamaño de la
# salida de la función hash (32 bytes). Con una clave más corta la firma es
# más barata de atacar por fuerza bruta que el propio SHA-256.
LONGITUD_MINIMA_CLAVE_JWT = 32


class ClaveJWTDebilWarning(UserWarning):
    """Aviso de arranque: la clave JWT no alcanza el mínimo de RFC 7518.

    Fuera de producción no se aborta (el desarrollo local y las pruebas deben
    poder arrancar), pero el aviso debe ser visible y atribuible a este
    proyecto, no un mensaje genérico de la librería de JWT."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://user:pass@localhost:5432/farmacia_db"

    @field_validator("database_url")
    @classmethod
    def _normalizar_database_url(cls, valor: str) -> str:
        """HU-56: Railway (y Heroku, Render…) entregan `postgres://` o
        `postgresql://`, que SQLAlchemy resolvería con psycopg2 —no instalado—
        en vez de asyncpg. asyncpg tampoco entiende `sslmode`, sino `ssl`.
        Normalizar aquí cubre a la vez la app y Alembic (env.py lee Settings)."""
        return normalizar_database_url(valor)

    jwt_secret_key: str = _SECRETO_POR_DEFECTO
    # BE-022: solo HMAC. Un valor arbitrario (p. ej. "none" o uno asimétrico
    # con la clave simétrica) se rechaza al arrancar, no al primer login.
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_issuer: str = "cadena-frio-backend"
    jwt_audience: str = "cadena-frio-api"

    # Tickets efímeros para autenticar el stream SSE (EventSource no envía
    # el header Authorization, así que se emite un JWT de un solo propósito).
    sse_ticket_expire_seconds: int = 60

    # Límite de intentos de login por IP dentro de la ventana deslizante.
    login_max_intentos: int = 5
    login_ventana_segundos: int = 300
    # BE-008: límite por CUENTA, independiente de la IP. Frena la adivinación
    # distribuida (muchas IPs contra un mismo correo) que el límite por IP no
    # ve, y no depende de que el proxy reporte bien la IP del cliente.
    login_cuenta_max_intentos: int = 10
    login_cuenta_ventana_segundos: int = 900

    # Límite global de solicitudes por IP (protección DoS de capa aplicación,
    # OWASP API4). /health queda exento para los probes de la plataforma.
    api_rate_limit_habilitado: bool = True
    api_rate_limit_max_solicitudes: int = 240
    api_rate_limit_ventana_segundos: int = 60
    security_state_max_entries: int = 10_000

    # Cuota propia de la ingesta REST de lecturas (B13). Es la vía secundaria:
    # el reenvío del buffer offline del ESP32 viaja por MQTT (RF-06) y no pasa
    # por aquí, así que este techo no compromete el RNF-07 (sync ≤30 s). 120/min
    # equivale a dos lecturas por segundo, muy por encima del muestreo real de
    # un refrigerador y muy por debajo de lo que cuesta saturar la API.
    ingesta_rate_limit_max_solicitudes: int = 120
    ingesta_rate_limit_ventana_segundos: int = 60

    # Tamaño máximo del cuerpo de una request (payloads IoT y formularios son
    # pequeños; cualquier cosa mayor es anómala).
    max_body_bytes: int = 64 * 1024

    # BE-019: operaciones que recorren toda la cadena o generan reportes que
    # se ejecutan a la vez en el proceso, y streams SSE simultáneos por
    # usuario. Por encima, 503/429 en vez de agotar memoria o conexiones.
    operaciones_costosas_concurrentes: int = 2
    sse_max_streams_por_usuario: int = 3

    # Mínimo privilegio de dispositivos: en estricto, solo device_id
    # provisionados en la tabla `devices` pueden registrar lecturas.
    device_registry_estricto: bool = True

    # BE-007: clave HMAC de los eslabones de trazabilidad (v3). Fuera de la
    # base de datos: quien solo pueda escribir en PostgreSQL no puede
    # recalcular la cadena. Vacía = eslabones v2 (SHA-256 sin clave), solo
    # admitido fuera de producción.
    trazabilidad_hmac_key: str = ""
    # BE-002: API REST de administración del broker (EMQX). Aprovisiona y
    # elimina las credenciales de los dispositivos en la autenticación integrada
    # del broker (usuario = device_id, contraseña = token) y expulsa su sesión
    # al revocar o rotar. Sin estos valores el token emitido no queda
    # aprovisionado en el broker.
    broker_api_url: str = ""
    broker_api_key: str = ""
    broker_api_secret: str = ""
    # Quién gestiona los usuarios (credenciales de dispositivo) de la autenticación
    # integrada del broker:
    #   api: el backend los crea, reemplaza y elimina con la API de administración
    #     (EMQX 5 completo, p. ej. el de infra-local o un plan Dedicated).
    #   manual: se crean y retiran en la consola de la instancia (EMQX Cloud Serverless,
    #     cuya API solo cubre clientes, suscripciones y publicación). El backend sigue
    #     emitiendo el token, guarda su hash y expulsa la sesión por API, pero informa
    #     que el aprovisionamiento en el broker queda pendiente.
    broker_usuarios_modo: Literal["api", "manual"] = "api"

    # Hosts permitidos en producción (mitiga ataques por header Host).
    allowed_hosts: list[str] = []

    # Acceso con Google (RF-17, método alternativo).
    #
    # Google verifica la IDENTIDAD; la AUTORIZACIÓN sigue siendo la tabla
    # `users`: solo entran correos que un administrador dio de alta, y el rol
    # sale de la base de datos. Deshabilitado por defecto: sin `client_id` no
    # hay forma de comprobar que un ID token fue emitido para esta aplicación
    # y no para cualquier otra de las que usan Google.
    google_oauth_enabled: bool = False
    google_client_id: str = ""

    password_min_length: int = 10

    # HU-23: aviso fuera de la aplicación ante excursión crítica. Ambos canales
    # llegan deshabilitados: sin credenciales configuradas el sistema funciona
    # igual, solo sin notificar. Las credenciales viven en el entorno (RNF-05),
    # nunca en el código.
    # BE-025: página del frontend que recibe el enlace de recuperación de
    # contraseña (el token viaja en el fragmento `#token=`). Se envía por el
    # mismo SMTP de las alertas.
    password_reset_url: str = "http://localhost:5173/restablecer-password"
    #
    # HU-53: el correo tiene dos transportes. `smtp` (SSL en 465 o STARTTLS en
    # 587) y `http_api` (proveedor transaccional por HTTPS, compatible con la
    # API de Resend), para plataformas que restringen el SMTP saliente.
    # `SMTP_ENABLED` se conserva como sinónimo de `EMAIL_ENABLED` para no romper
    # despliegues que ya lo usaban.
    email_enabled: bool = False
    email_transporte: Literal["smtp", "http_api"] = "smtp"
    email_from: str = ""
    # Destinatario cuando el dispositivo no tiene `responsable_email` propio.
    email_destino_respaldo: str = ""
    email_api_url: str = "https://api.resend.com/emails"
    email_api_key: str = ""

    smtp_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_seguridad: Literal["ssl", "starttls"] = "ssl"
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_to: str = ""

    # Base del enlace a la alerta que lleva el correo (FRONTEND_URL/alertas?id=…).
    frontend_url: str = ""

    # HU-53: reintentos del aviso ante fallo del proveedor (backoff 30 s, 2 min, 5 min).
    notificacion_max_intentos: int = 3

    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # HU-54 (backlog 54 HU): canal SMS opcional, "sujeto a viabilidad y
    # configuración del proveedor" — se modela contra una pasarela
    # compatible con la API REST de Twilio (Account SID + Auth Token, la
    # forma más extendida entre proveedores SMS), sin atarse a un SDK
    # propietario. `sms_from` es el número/ID remitente que exige el
    # proveedor; el destinatario es el teléfono del responsable registrado
    # en cada dispositivo (devices.responsable_telefono), no uno global.
    sms_enabled: bool = False
    sms_api_url: str = "https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    sms_account_sid: str = ""
    sms_auth_token: str = ""
    sms_from: str = ""

    # Un episodio crítico genera una lectura cada pocos segundos; sin esta
    # ventana el responsable recibiría cientos de avisos y silenciaría el canal.
    notificacion_cooldown_minutos: int = 15

    # HU-20: ventana de normalización configurable — un episodio de alerta
    # cerrado y reabierto dentro de esta ventana se trata como continuación
    # del mismo episodio en vez de crear uno nuevo (evita flapping).
    alerta_ventana_normalizacion_minutos: int = 15

    # HU-07: antigüedad máxima de una lectura del nodo (reenvío del buffer
    # LittleFS tras un corte). Más vieja que esto va a cuarentena como
    # `timestamp_demasiado_antiguo`. 168 h = 7 días.
    lectura_antiguedad_max_horas: int = Field(default=168, ge=1, le=24 * 90)

    # HU-54: un nodo está SIN_DATOS cuando calla N veces su intervalo de captura
    # (devices.intervalo_reporte_segundos, 30 s de fábrica). La tarea periódica
    # revisa a todos los nodos cada SIN_DATOS_PERIODO_SEGUNDOS.
    sin_datos_n_intervalos: int = Field(default=3, ge=2, le=20)
    sin_datos_periodo_segundos: int = Field(default=30, ge=5, le=600)

    # HU-35 / HU-04 esc. 3: segundos de apertura continua a partir de los
    # cuales el dashboard destaca la puerta abierta como prolongada.
    puerta_umbral_prolongada_segundos: int = 60

    # Modelo preventivo temporal (docs/ml/06_BACKEND_INTEGRATION.md).
    # desactivado: el flujo de lecturas es exactamente el anterior.
    # sombra: predice y guarda en `predicciones_preventivas` sin afectar
    #   nivel de riesgo, alertas ni trazabilidad (evidencia prospectiva).
    # activo: decide `nivel_riesgo`; solo carga un modelo APPROVED_FOR_DEPLOYMENT.
    # La versión es un directorio de src/infrastructure/ai/models/preventivo/;
    # el SHA-256 fija el artefacto aprobado (rollback = otra versión + su hash).
    ia_preventivo_modo: Literal["desactivado", "sombra", "activo"] = "desactivado"
    ia_preventivo_version: str = ""
    ia_preventivo_sha256: str = ""

    # HU-20: modelo Random Forest legado, entrenado con escenarios SINTÉTICOS
    # (docs/ml/01_DATASET_AUDIT.md). No hay un modelo validado con datos reales:
    #   experimental: clasifica y su clase queda como evidencia marcada
    #     `ia_experimental`, pero NO abre alertas preventivas; el riesgo
    #     efectivo lo deciden la regla de rango 2-8 °C y la salvaguarda.
    #   desactivado: no se ejecuta; solo regla de rango y salvaguarda.
    #   activo: comportamiento anterior (su clase abre alertas preventivas).
    ia_legado_modo: Literal["experimental", "desactivado", "activo"] = "experimental"

    cors_origins: list[str] = ["http://localhost:5173"]

    mqtt_host: str = "tu-instancia.emqx.cloud"
    mqtt_port: int = 8883
    mqtt_username: str = "backend_service"
    mqtt_password: str = "token_seguro"
    mqtt_client_id: str = "backend-farmacia-cdl"
    mqtt_tls_enabled: bool = True
    # CA privada del broker (banco local). Vacío = almacén del sistema, que es
    # lo correcto con EMQX Cloud (Let's Encrypt).
    mqtt_ca_file: str = ""
    mqtt_enabled: bool = True

    # Solo se aceptan entornos con semántica de seguridad conocida. Un valor
    # como "prod" no debe omitir silenciosamente los controles de production.
    environment: Literal["development", "test", "production"] = "development"

    @property
    def correo_habilitado(self) -> bool:
        return self.email_enabled or self.smtp_enabled

    @property
    def correo_remitente(self) -> str:
        return self.email_from or self.smtp_from

    @property
    def correo_destino_respaldo(self) -> str:
        return self.email_destino_respaldo or self.smtp_to

    @model_validator(mode="after")
    def _validar_longitud_clave_jwt(self) -> "Settings":
        """La longitud mínima de clave se comprueba en TODOS los entornos.

        En producción es un error de arranque (lo aplica
        `_validar_secretos_en_produccion`); en desarrollo y pruebas se avisa,
        porque una clave corta en el entorno de trabajo acaba copiándose al
        despliegue con demasiada facilidad.
        """
        if (
            self.environment != "production"
            and len(self.jwt_secret_key.encode()) < LONGITUD_MINIMA_CLAVE_JWT
        ):
            warnings.warn(
                f"JWT_SECRET_KEY mide {len(self.jwt_secret_key.encode())} bytes; el mínimo "
                f"recomendado para HS256 es {LONGITUD_MINIMA_CLAVE_JWT} (RFC 7518 §3.2).",
                ClaveJWTDebilWarning,
                stacklevel=2,
            )
        return self

    @model_validator(mode="after")
    def _validar_secretos_en_produccion(self) -> "Settings":
        if self.environment == "production":
            if (
                self.jwt_secret_key == _SECRETO_POR_DEFECTO
                or len(self.jwt_secret_key.encode()) < LONGITUD_MINIMA_CLAVE_JWT
            ):
                raise ValueError(
                    "JWT_SECRET_KEY debe ser un secreto aleatorio de al menos 32 bytes "
                    "en producción (no usar el valor por defecto)."
                )
            publicados = [
                nombre
                for nombre, valor in (
                    ("JWT_SECRET_KEY", self.jwt_secret_key),
                    ("TRAZABILIDAD_HMAC_KEY", self.trazabilidad_hmac_key),
                )
                if valor and _secreto_publicado(valor)
            ]
            if publicados:
                raise ValueError(
                    f"{', '.join(publicados)} usa un valor publicado en el historial del "
                    "repositorio; genere uno nuevo y aleatorio."
                )
            if any(origen == "*" for origen in self.cors_origins):
                raise ValueError("CORS_ORIGINS no puede contener '*' en producción.")
            if not self.allowed_hosts:
                raise ValueError(
                    "ALLOWED_HOSTS debe declararse explícitamente en producción."
                )
            if self.database_url == "postgresql+asyncpg://user:pass@localhost:5432/farmacia_db":
                raise ValueError("DATABASE_URL no puede usar el valor de ejemplo en producción.")
            if not self.cors_origins or any(
                not origen.startswith("https://") for origen in self.cors_origins
            ):
                raise ValueError("CORS_ORIGINS debe declarar únicamente orígenes HTTPS en producción.")
            # Habilitar el acceso con Google sin client_id dejaría el endpoint
            # publicado sin poder validar `aud`: aceptaría ID tokens emitidos
            # para cualquier otra aplicación de Google.
            if self.google_oauth_enabled and not self.google_client_id:
                raise ValueError(
                    "GOOGLE_OAUTH_ENABLED requiere GOOGLE_CLIENT_ID (sin él no se "
                    "puede validar la audiencia del ID token)."
                )
            if len(self.trazabilidad_hmac_key.encode()) < 32:
                raise ValueError(
                    "TRAZABILIDAD_HMAC_KEY debe ser un secreto aleatorio de al menos 32 bytes en producción."
                )
            if self.trazabilidad_hmac_key == self.jwt_secret_key:
                raise ValueError("TRAZABILIDAD_HMAC_KEY no puede reutilizar JWT_SECRET_KEY.")
            if self.mqtt_enabled:
                # RNF-05: transmisión sobre TLS y credenciales fuera del código fuente.
                if not self.mqtt_tls_enabled:
                    raise ValueError("MQTT_TLS_ENABLED debe ser true en producción (RNF-05).")
                if self.mqtt_password in ("token_seguro", ""):
                    raise ValueError(
                        "MQTT_PASSWORD no puede ser el valor por defecto en producción (RNF-05)."
                    )
                if self.mqtt_host == "tu-instancia.emqx.cloud":
                    raise ValueError("MQTT_HOST no puede usar el valor de ejemplo en producción.")
            # Un modelo que decide el nivel de riesgo en producción debe estar
            # identificado por versión Y por el hash del artefacto aprobado: sin
            # el hash, cambiar el archivo bastaría para cambiar las decisiones.
            if self.ia_preventivo_modo == "activo" and not (
                self.ia_preventivo_version and len(self.ia_preventivo_sha256) == 64
            ):
                raise ValueError(
                    "IA_PREVENTIVO_MODO=activo requiere IA_PREVENTIVO_VERSION e IA_PREVENTIVO_SHA256 en producción."
                )
            if self.correo_habilitado:
                if not (self.correo_remitente and self.correo_destino_respaldo):
                    raise ValueError(
                        "El correo (EMAIL_ENABLED/SMTP_ENABLED) requiere remitente "
                        "(EMAIL_FROM o SMTP_FROM) y destinatario de respaldo "
                        "(EMAIL_DESTINO_RESPALDO o SMTP_TO)."
                    )
                if self.email_transporte == "smtp" and not all(
                    (self.smtp_host, self.smtp_user, self.smtp_password)
                ):
                    raise ValueError(
                        "EMAIL_TRANSPORTE=smtp requiere SMTP_HOST, SMTP_USER y SMTP_PASSWORD."
                    )
                if self.email_transporte == "http_api" and not (
                    self.email_api_url.startswith("https://") and self.email_api_key
                ):
                    raise ValueError(
                        "EMAIL_TRANSPORTE=http_api requiere EMAIL_API_URL (https) y EMAIL_API_KEY."
                    )
                if not self.frontend_url.startswith("https://"):
                    raise ValueError(
                        "FRONTEND_URL (https) es obligatoria con el correo habilitado: "
                        "el aviso enlaza a la alerta."
                    )
            # HU-23: habilitar un canal de aviso sin sus credenciales dejaría
            # al sistema creyendo que notifica cuando en realidad falla en cada
            # excursión crítica — el peor modo de fallo posible aquí.
            if self.smtp_enabled and not all(
                (self.smtp_host, self.smtp_user, self.smtp_password, self.smtp_from, self.smtp_to)
            ):
                raise ValueError(
                    "SMTP_ENABLED requiere SMTP_HOST, SMTP_USER, SMTP_PASSWORD, SMTP_FROM y SMTP_TO."
                )
            # BE-025: con SMTP habilitado se envían enlaces de recuperación; el
            # token no puede viajar a una página sin TLS.
            if self.smtp_enabled and not self.password_reset_url.startswith("https://"):
                raise ValueError("PASSWORD_RESET_URL debe ser una URL HTTPS en producción.")
            if self.telegram_enabled and not all(
                (self.telegram_bot_token, self.telegram_chat_id)
            ):
                raise ValueError(
                    "TELEGRAM_ENABLED requiere TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID."
                )
            if self.sms_enabled and not all(
                (self.sms_account_sid, self.sms_auth_token, self.sms_from)
            ):
                raise ValueError(
                    "SMS_ENABLED requiere SMS_ACCOUNT_SID, SMS_AUTH_TOKEN y SMS_FROM."
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
