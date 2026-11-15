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


_URL_API_RESEND = "https://api.resend.com/emails"
_URL_RESTABLECER_POR_DEFECTO = "http://localhost:5173/restablecer-password"

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

    # Códigos de verificación de un solo uso (6 dígitos) enviados por correo:
    # segundo factor del login, recuperación de contraseña y activación de
    # cuentas creadas por un administrador. Se guardan como HMAC-SHA256 con
    # CODIGOS_HMAC_KEY (secreto aparte del JWT y de la trazabilidad).
    #
    # OTP_LOGIN_ENABLED: interruptor del segundo factor del login con
    # contraseña. Apagado, POST /api/auth/login se comporta exactamente como
    # antes (interruptor de emergencia si el correo deja de salir).
    otp_login_enabled: bool = False
    codigo_ttl_minutos: int = Field(default=10, ge=1, le=60)
    codigo_activacion_ttl_horas: int = Field(default=48, ge=1, le=24 * 14)
    # Intentos fallidos admitidos por código; al agotarlos el código se quema.
    codigo_max_intentos: int = Field(default=5, ge=1, le=20)
    # Espera mínima entre dos envíos del código de un mismo inicio de sesión.
    codigo_reenvio_segundos: int = Field(default=60, ge=0, le=3600)
    # Tope de intentos fallidos por cuenta sumando todos los códigos (login, o
    # recuperación+activación) emitidos en la ventana. Superado, no se emiten
    # ni se aceptan códigos de ese tipo hasta que la ventana pase o un
    # administrador restablezca la contraseña. Vive en la base, no en memoria.
    codigo_max_fallos_cuenta: int = Field(default=20, ge=5, le=200)
    codigo_ventana_fallos_horas: int = Field(default=24, ge=1, le=24 * 7)
    codigos_hmac_key: str = ""

    # HU-23: aviso fuera de la aplicación ante excursión crítica. Ambos canales
    # llegan deshabilitados: sin credenciales configuradas el sistema funciona
    # igual, solo sin notificar. Las credenciales viven en el entorno (RNF-05),
    # nunca en el código.
    # BE-025: página del frontend que recibe el enlace de recuperación de
    # contraseña (el token viaja en el fragmento `#token=`). Se envía por el
    # mismo SMTP de las alertas.
    password_reset_url: str = _URL_RESTABLECER_POR_DEFECTO
    #
    # HU-53: el correo tiene dos transportes. `smtp` (SSL en 465 o STARTTLS en
    # 587) y `http_api` (proveedor transaccional por HTTPS, compatible con la
    # API de Resend), para plataformas que restringen el SMTP saliente.
    # `SMTP_ENABLED` se conserva como sinónimo de `EMAIL_ENABLED` para no romper
    # despliegues que ya lo usaban.
    #
    # `apps_script`: relé HTTPS propio desplegado como aplicación web de Google
    # Apps Script (envía con la cuenta de Gmail/Workspace que lo publica). Recibe
    # POST JSON en EMAIL_API_URL con el secreto compartido EMAIL_API_KEY en el
    # cuerpo y responde {"ok": true}. Ver docs/operacion/CODIGOS_VERIFICACION.md.
    email_enabled: bool = False
    email_transporte: Literal["smtp", "http_api", "apps_script"] = "smtp"
    email_from: str = ""
    # Nombre visible del remitente (lo usa el relé de Apps Script).
    email_from_nombre: str = "ColdCare"
    # Destinatario cuando el dispositivo no tiene `responsable_email` propio.
    email_destino_respaldo: str = ""
    email_api_url: str = _URL_API_RESEND
    email_api_key: str = ""

    smtp_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    # `ninguna` (SMTP en claro, sin login) solo para Mailpit/servidores de prueba:
    # se rechaza con ENVIRONMENT=production.
    smtp_seguridad: Literal["ssl", "starttls", "ninguna"] = "ssl"
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

    # HU-53 (v3.3): el canal SMS se retiró del alcance; el aviso externo es
    # solo por correo (HU-52). Las variables SMS_* de despliegues anteriores
    # (SMS_ENABLED, SMS_API_URL, SMS_ACCOUNT_SID, SMS_AUTH_TOKEN, SMS_FROM) ya
    # no son campos: `extra="ignore"` las descarta sin impedir el arranque.

    # Un episodio crítico genera una lectura cada pocos segundos; sin esta
    # ventana el responsable recibiría cientos de avisos y silenciaría el canal.
    notificacion_cooldown_minutos: int = 15

    # HU-20: ventana de normalización configurable — un episodio de alerta
    # cerrado y reabierto dentro de esta ventana se trata como continuación
    # del mismo episodio en vez de crear uno nuevo (evita flapping).
    alerta_ventana_normalizacion_minutos: int = 15
    # HU-20 esc. 3: minutos SEGUIDOS en Normal que necesita una alerta
    # preventiva abierta para resolverse (una lectura preventiva reinicia la
    # cuenta). 0 = se cierra con la primera lectura normal (comportamiento
    # anterior). Con los modos de IA por defecto no se abren preventivas, así
    # que este valor solo actúa con un modelo aprobado (o el legado «activo»).
    alerta_preventiva_minutos_normales: int = Field(default=10, ge=0, le=240)

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
    # La alerta crítica (regla 2-8 °C) nunca espera a la IA: clasificar y predecir
    # corren en un hilo con este tope. Si vence, la lectura queda con
    # estado_inferencia=fallida y sigue la regla determinista.
    ia_inferencia_timeout_s: float = Field(default=1.0, gt=0)
    # SOLO PRUEBAS: simulan una inferencia lenta o que lanza una excepción
    # (casos RIE-01, RIE-03, LAT-02). El arranque se niega con ENVIRONMENT=production.
    ia_prueba_retardo_s: float = Field(default=0.0, ge=0)
    ia_prueba_excepcion: bool = False
    # SOLO PRUEBAS (MQT-01): falla justo ANTES del COMMIT de una lectura. `excepcion`
    # lanza un error; `salir` mata el proceso sin cierre ordenado (os._exit).
    # Los modos `*_una_vez` fallan SOLO la primera lectura tras armar el interruptor:
    # dejan un archivo marca (MQTT_PRUEBA_FALLO_MARCA) y, mientras exista, no vuelven
    # a fallar, ni siquiera tras el reinicio del proceso. Para repetir el caso, borrar
    # la marca. Sin esto cada una de las 10 repeticiones de MQT-01 pide reiniciar.
    mqtt_prueba_fallo_antes_commit: Literal["", "excepcion", "salir", "excepcion_una_vez", "salir_una_vez"] = ""
    mqtt_prueba_fallo_marca: str = "/tmp/coldcare_fallo_una_vez"
    # HU-46: criterio que debe cumplir una versión para activarse (decisión con
    # el asesor). `por_incidente`: sensibilidad por incidente, IC95, falsas
    # alarmas/día y anticipación; `f1`: F1 macro ≥ 0,85. Ambos se reportan.
    ia_criterio_principal: Literal["por_incidente", "f1"] = "por_incidente"

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
    def base_frontend(self) -> str:
        """Origen del frontend: FRONTEND_URL, o el de PASSWORD_RESET_URL si no se declaró."""
        if self.frontend_url:
            return self.frontend_url.rstrip("/")
        esquema, _, resto = self.password_reset_url.partition("://")
        return f"{esquema}://{resto.split('/', 1)[0]}" if resto else ""

    @property
    def url_restablecer(self) -> str:
        """Página del enlace de recuperación (legado). Si PASSWORD_RESET_URL quedó en su
        valor de desarrollo pero hay FRONTEND_URL, el enlace apunta a ese frontend."""
        if self.password_reset_url == _URL_RESTABLECER_POR_DEFECTO and self.frontend_url:
            return f"{self.frontend_url.rstrip('/')}/restablecer-password"
        return self.password_reset_url

    @property
    def url_codigo_cuenta(self) -> str:
        """Página del frontend donde se ingresa un código de recuperación o activación."""
        return f"{self.base_frontend}/recuperar-contrasena"

    @property
    def clave_codigos(self) -> bytes:
        """Clave HMAC de los códigos de verificación.

        Fuera de producción, si CODIGOS_HMAC_KEY está vacía, se deriva de forma
        determinista de JWT_SECRET_KEY (desarrollo y pruebas arrancan sin
        configurar nada). En producción la clave propia es obligatoria."""
        if self.codigos_hmac_key:
            return self.codigos_hmac_key.encode()
        if self.environment == "production":
            raise RuntimeError("CODIGOS_HMAC_KEY no está configurada.")
        return hashlib.sha256(b"coldcare-codigos-verificacion:" + self.jwt_secret_key.encode()).digest()

    @property
    def correo_destino_respaldo(self) -> str:
        """Uno o varios correos separados por coma: todos reciben el aviso."""
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
    def _rechazar_opciones_de_prueba_en_produccion(self) -> "Settings":
        """Las opciones de inyección de fallos existen solo para el banco de pruebas."""
        if self.environment == "production":
            activas = [
                nombre
                for nombre, valor in (
                    ("IA_PRUEBA_RETARDO_S", self.ia_prueba_retardo_s > 0),
                    ("IA_PRUEBA_EXCEPCION", self.ia_prueba_excepcion),
                    ("MQTT_PRUEBA_FALLO_ANTES_COMMIT", bool(self.mqtt_prueba_fallo_antes_commit)),
                    ("SMTP_SEGURIDAD=ninguna", self.smtp_seguridad == "ninguna"),
                )
                if valor
            ]
            if activas:
                raise ValueError(
                    f"{', '.join(activas)} inyecta fallos y no puede activarse con ENVIRONMENT=production."
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
                if self.email_transporte == "apps_script" and not (
                    self.email_api_url.startswith("https://")
                    and self.email_api_url != _URL_API_RESEND
                    and self.email_api_key
                ):
                    raise ValueError(
                        "EMAIL_TRANSPORTE=apps_script requiere EMAIL_API_URL (https, la URL /exec "
                        "del despliegue de Apps Script) y EMAIL_API_KEY (secreto compartido)."
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
            # BE-025: con cualquier transporte de correo (SMTP, http_api o
            # apps_script) se envían enlaces de recuperación; el token no puede
            # viajar a una página sin TLS. Se valida la URL efectiva (incluido
            # el respaldo en FRONTEND_URL).
            if self.correo_habilitado and not self.url_restablecer.startswith("https://"):
                raise ValueError("PASSWORD_RESET_URL debe ser una URL HTTPS en producción.")
            if self.telegram_enabled and not all(
                (self.telegram_bot_token, self.telegram_chat_id)
            ):
                raise ValueError(
                    "TELEGRAM_ENABLED requiere TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID."
                )
            self._validar_codigos_en_produccion()
        return self

    def _validar_codigos_en_produccion(self) -> None:
        """Segundo factor y códigos de un solo uso. Fallar cerrado: con el
        login por código activo y sin correo nadie podría entrar, y con una
        clave HMAC débil o compartida los códigos guardados serían atacables
        por fuerza bruta fuera de línea (solo hay 10^6 valores posibles)."""
        if self.otp_login_enabled and not self.correo_habilitado:
            raise ValueError(
                "OTP_LOGIN_ENABLED=true requiere el correo habilitado (EMAIL_ENABLED y su transporte): "
                "sin él nadie podría recibir el código de acceso."
            )
        if not (self.otp_login_enabled or self.correo_habilitado or self.codigos_hmac_key):
            return
        clave = self.codigos_hmac_key
        if len(clave.encode()) < 32:
            raise ValueError(
                "CODIGOS_HMAC_KEY debe ser un secreto aleatorio de al menos 32 bytes en producción."
            )
        if clave in (self.jwt_secret_key, self.trazabilidad_hmac_key):
            raise ValueError(
                "CODIGOS_HMAC_KEY no puede reutilizar JWT_SECRET_KEY ni TRAZABILIDAD_HMAC_KEY."
            )
        if _secreto_publicado(clave):
            raise ValueError(
                "CODIGOS_HMAC_KEY usa un valor publicado en el historial del repositorio; genere uno nuevo."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
