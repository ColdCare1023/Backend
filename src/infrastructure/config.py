import warnings
from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SECRETO_POR_DEFECTO = "clave_secreta_larga_y_aleatoria_cambiar_en_produccion"

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

    jwt_secret_key: str = _SECRETO_POR_DEFECTO
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_issuer: str = "cadena-frio-backend"
    jwt_audience: str = "cadena-frio-api"

    # Tickets efímeros para autenticar el stream SSE (EventSource no envía
    # el header Authorization, así que se emite un JWT de un solo propósito).
    sse_ticket_expire_seconds: int = 60

    # Límite de intentos de login por IP dentro de la ventana deslizante.
    login_max_intentos: int = 5
    login_ventana_segundos: int = 300

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

    # Mínimo privilegio de dispositivos: en estricto, solo device_id
    # provisionados en la tabla `devices` pueden registrar lecturas.
    device_registry_estricto: bool = True

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
    smtp_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_to: str = ""

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

    cors_origins: list[str] = ["http://localhost:5173"]

    mqtt_host: str = "tu-instancia.emqx.cloud"
    mqtt_port: int = 8883
    mqtt_username: str = "backend_service"
    mqtt_password: str = "token_seguro"
    mqtt_client_id: str = "backend-farmacia-cdl"
    mqtt_tls_enabled: bool = True
    mqtt_enabled: bool = True

    # Solo se aceptan entornos con semántica de seguridad conocida. Un valor
    # como "prod" no debe omitir silenciosamente los controles de production.
    environment: Literal["development", "test", "production"] = "development"

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
            # HU-23: habilitar un canal de aviso sin sus credenciales dejaría
            # al sistema creyendo que notifica cuando en realidad falla en cada
            # excursión crítica — el peor modo de fallo posible aquí.
            if self.smtp_enabled and not all(
                (self.smtp_host, self.smtp_user, self.smtp_password, self.smtp_from, self.smtp_to)
            ):
                raise ValueError(
                    "SMTP_ENABLED requiere SMTP_HOST, SMTP_USER, SMTP_PASSWORD, SMTP_FROM y SMTP_TO."
                )
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
