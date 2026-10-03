from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

# HU-15/HU-05: estados de sensor válidos para acompañar un valor null. Un
# sensor que reporta null SIN uno de estos estados es un payload malformado
# (se rechaza), no una falla de sensor legítima (se acepta).
ESTADOS_SENSOR_VALIDOS = frozenset({"ok", "sensor_error", "fuera_de_rango", "no_instalado"})

# HU-05: versiones de esquema de payload que este backend sabe interpretar.
# Un firmware desactualizado o mal configurado que declare una versión fuera
# de este conjunto se rechaza explícitamente en vez de intentar interpretar
# un contrato que el backend no conoce (criterio 4 de HU-05).
SCHEMA_VERSIONS_SOPORTADAS = frozenset({1})
SCHEMA_VERSION_ACTUAL = 1

# Techo del cuerpo de un mensaje MQTT aceptado. `PayloadBuilder::build()` del
# firmware descarta cualquier payload de más de 512 bytes antes de publicarlo
# (ver PayloadCore.h), así que este margen es diez veces el máximo legítimo.
#
# Existe porque la ingesta MQTT no atraviesa el middleware HTTP que acota el
# cuerpo de las peticiones REST (`max_body_bytes`): sin él, quien tuviera
# credenciales del broker podía obligar al backend a materializar en memoria un
# mensaje arbitrariamente grande, en una instancia de 512 MB.
MAX_BYTES_PAYLOAD_MQTT = 5 * 1024


class LecturaPayload(BaseModel):
    """Valida el payload JSON publicado por el firmware ESP32 (ver README sección 3)."""

    # El contrato MQTT es cerrado: no se silencian campos inesperados y NaN/
    # infinito nunca llega a persistencia, IA ni trazabilidad.
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    device_id: str = Field(min_length=1, max_length=50)
    timestamp: datetime
    # HU-05: versión del contrato de payload. Por defecto 1 (compatibilidad
    # con firmware ya desplegado que aún no lo declara explícitamente); un
    # valor fuera de SCHEMA_VERSIONS_SOPORTADAS se rechaza en el validador
    # de abajo en vez de interpretarse a ciegas.
    schema_version: int = Field(default=SCHEMA_VERSION_ACTUAL, ge=1)
    # HU-05: identificador lógico de ESTA lectura, generado por el firmware.
    # Optativo por compatibilidad hacia atrás (dispositivos ya desplegados
    # que aún no lo envían); cuando está presente, registrar_lectura_termica
    # lo usa como clave de idempotencia además de (device_id, timestamp).
    reading_id: str | None = Field(default=None, max_length=100)
    # HU-01/HU-11 (backlog 54 HU): identidad lógica real de la lectura para
    # idempotencia — boot_id incrementa en cada arranque del ESP32, seq_no es
    # monótono dentro de ese boot. Optativos por compatibilidad hacia atrás;
    # cuando AMBOS están presentes, son la clave de idempotencia preferida
    # sobre reading_id/(device_id, timestamp).
    boot_id: int | None = Field(default=None, ge=0)
    seq_no: int | None = Field(default=None, ge=0)
    # HU-01: calidad de la sincronización temporal del dispositivo en el
    # instante de captura. "synced" cuando hay NTP/RTC válido; "unsynced"
    # cuando captured_at se estimó sin referencia externa confiable. Optativo
    # por compatibilidad con firmware que aún no lo declara.
    time_quality: Literal["synced", "unsynced"] | None = None
    # Acepta contrato interno y nombres de firmware/simulador. Se serializa
    # siempre con los nombres internos, así no rompe backend existente.
    message_id: str | None = Field(default=None, max_length=100)
    temperatura_ambiental: float | None = Field(
        default=None, ge=-40.0, le=125.0,
        validation_alias=AliasChoices("temperatura_ambiental", "temperatura_sht31"),
    )
    humedad_ambiental: float | None = Field(
        default=None, ge=0.0, le=100.0,
        validation_alias=AliasChoices("humedad_ambiental", "humedad"),
    )
    temperatura_interna: float | None = Field(
        default=None, ge=-55.0, le=125.0,
        validation_alias=AliasChoices("temperatura_interna", "temperatura_ds18b20"),
    )
    # HU-04: `None` cuando el nodo no tiene MC-38 instalado (ver
    # `mc38_status`) — antes era `bool` no-opcional y un nodo sin MC-38 no
    # tenía forma de declarar "no aplica" sin simular una puerta cerrada
    # que en realidad no existe.
    apertura_refrigerador: bool | None = Field(
        default=False, validation_alias=AliasChoices("apertura_refrigerador", "puerta_abierta")
    )
    mc38_status: Literal["ok", "not_installed"] | None = None
    # El firmware solo emite estos dos valores (`lectura.online ? "online" :
    # "offline"`). Declararlo como `str` libre dejaba entrar cualquier cadena
    # hasta la columna `String(20)` de la base de datos, donde PostgreSQL la
    # rechaza con un error de escritura en vez de con un 422 en el borde.
    estado_conectividad: Literal["online", "offline"] = "online"
    # Mismo techo que `EventoDispositivoPayload.firmware_version` y que la
    # columna `devices.firmware_version`, que es String(20). Sin él, esta rama
    # del contrato admitía una cadena ilimitada que acababa en la columna JSONB
    # `payload` de cada lectura.
    firmware_version: str | None = Field(default=None, max_length=20)
    # HU-04: el nodo acompaña cada apertura de puerta con su duración
    # acumulada. `PayloadBuilder::build()` lo emite SIEMPRE (0 con la puerta
    # cerrada), así que sin este campo `extra="forbid"` rechazaba el 100% de
    # las lecturas del firmware real. Ver IoT-documentacion_iot.md §3.5.
    duracion_apertura_segundos: int = Field(default=0, ge=0)
    # HU-15 criterio 2: estado explícito por sensor, optativo por
    # compatibilidad hacia atrás. Cuando el firmware SÍ los envía, un valor
    # null debe venir acompañado de un estado distinto de "ok" — ver
    # `_estado_acompana_null_correctamente` — de lo contrario el mensaje se
    # rechaza como malformado en vez de aceptarse como "falla de sensor".
    estado_temperatura_interna: Literal["ok", "sensor_error", "fuera_de_rango"] | None = None
    estado_temperatura_ambiental: Literal["ok", "sensor_error", "fuera_de_rango"] | None = None
    estado_humedad_ambiental: Literal["ok", "sensor_error", "fuera_de_rango"] | None = None

    @field_validator("schema_version")
    @classmethod
    def _version_soportada(cls, valor: int) -> int:
        if valor not in SCHEMA_VERSIONS_SOPORTADAS:
            raise ValueError(
                f"schema_version {valor} no soportado; versiones válidas: "
                f"{sorted(SCHEMA_VERSIONS_SOPORTADAS)}"
            )
        return valor

    @model_validator(mode="after")
    def _estado_acompana_null_correctamente(self) -> "LecturaPayload":
        for campo_valor, campo_estado in (
            ("temperatura_interna", "estado_temperatura_interna"),
            ("temperatura_ambiental", "estado_temperatura_ambiental"),
            ("humedad_ambiental", "estado_humedad_ambiental"),
        ):
            valor = getattr(self, campo_valor)
            estado = getattr(self, campo_estado)
            if estado is None:
                continue  # firmware que aún no envía estado por sensor: compatibilidad hacia atrás
            if valor is None and estado == "ok":
                raise ValueError(f"{campo_valor} es null pero {campo_estado} dice 'ok'")
            if valor is not None and estado != "ok":
                raise ValueError(f"{campo_valor} tiene un valor pero {campo_estado} no es 'ok'")
        return self


class TipoEventoDispositivo(StrEnum):
    """B-09: eventos que el nodo publica en `farmacias/{device_id}/eventos`,
    separados del flujo de lecturas."""

    LWT_ONLINE = "lwt_online"
    LWT_OFFLINE = "lwt_offline"
    ERROR_SENSOR = "error_sensor"
    FIRMWARE_UPDATE = "firmware_update"
    # HU-06 criterio 3: el buffer offline (LittleFS) alcanzó su tope y aplicó
    # la política FIFO — ninguna pérdida de lecturas queda silenciosa.
    BUFFER_SATURADO = "buffer_saturado"
    # HU-08 criterio 3: el nodo se reconectó Wi-Fi/MQTT después de superar el
    # umbral operativo de reintentos fallidos. Solo puede reportarse AL
    # reconectar (sin red no hay forma de avisar antes); `detalle` trae
    # cuántos intentos y cuánto duró el episodio.
    WIFI_RECONEXION_PROLONGADA = "wifi_reconexion_prolongada"
    # HU-52 (backlog 54 HU): continuidad de medición ante corte de suministro
    # eléctrico. CONMUTACION_RESPALDO: el nodo pasó a alimentarse del
    # respaldo de 5V exclusivo para ESP32+sensores (no el refrigerador ni el
    # router). RESPALDO_BAJO: el nivel de respaldo cayó bajo el umbral
    # configurado — diagnóstico antes de un eventual apagado ordenado; la
    # autonomía real se mide en el piloto, no se presupone aquí.
    CONMUTACION_RESPALDO = "conmutacion_respaldo"
    RESPALDO_BAJO = "respaldo_bajo"


class EventoDispositivoPayload(BaseModel):
    """Mensaje de estado del dispositivo (incluido el Last Will and Testament
    que el broker publica cuando el ESP32 pierde la conexión sin despedirse).

    Antes de este esquema, todo mensaje del tópico `/eventos` se validaba
    contra `LecturaPayload`, fallaba y se descartaba en silencio: las
    desconexiones del nodo nunca llegaban a registrarse."""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1, max_length=50)
    tipo_evento: TipoEventoDispositivo
    timestamp: datetime
    detalle: str | None = Field(default=None, max_length=500)
    firmware_version: str | None = Field(default=None, max_length=20)
