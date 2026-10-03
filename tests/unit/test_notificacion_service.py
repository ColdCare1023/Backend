"""HU-23: aviso fuera de la aplicación ante excursión crítica.

Ningún test toca la red: se sustituyen los canales por dobles. Lo que se
verifica son las tres propiedades que importan — que avise, que no inunde y
que un canal caído nunca rompa la ingesta."""

from datetime import datetime, timedelta, timezone

import pytest

from src.infrastructure.config import Settings
from src.infrastructure.notifications.notificacion_service import NotificacionService


def _settings(**overrides) -> Settings:
    base = {
        "environment": "test",
        "smtp_enabled": True,
        "smtp_host": "smtp.example.org",
        "smtp_user": "u",
        "smtp_password": "p",
        "smtp_from": "alertas@example.org",
        "smtp_to": "responsable@example.org",
        "telegram_enabled": False,
    }
    return Settings(**{**base, **overrides})


class _ServicioEspia(NotificacionService):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.emails: list[tuple[str, str]] = []
        self.telegrams: list[tuple[str, str]] = []
        self.sms: list[tuple[str, str]] = []

    async def _enviar_email(self, device_id, temp_texto, timestamp, email_destino=None):
        self.emails.append((device_id, temp_texto))

    async def _enviar_telegram(self, device_id, temp_texto, timestamp):
        self.telegrams.append((device_id, temp_texto))

    async def _enviar_sms(self, device_id, temp_texto, timestamp, telefono_destino):
        self.sms.append((device_id, temp_texto))


@pytest.mark.asyncio
async def test_notifica_excursion_critica_por_email():
    servicio = _ServicioEspia(_settings())
    await servicio.notificar_excursion_critica("ESP32-01", 14.2, "2026-07-25T10:00:00+00:00")

    assert servicio.emails == [("ESP32-01", "14.2 °C")]


@pytest.mark.asyncio
async def test_ambos_canales_cuando_ambos_estan_habilitados():
    servicio = _ServicioEspia(
        _settings(telegram_enabled=True, telegram_bot_token="t", telegram_chat_id="c")
    )
    await servicio.notificar_excursion_critica("ESP32-01", 14.2, "2026-07-25T10:00:00+00:00")

    assert len(servicio.emails) == 1
    assert len(servicio.telegrams) == 1


@pytest.mark.asyncio
async def test_sin_canales_habilitados_no_hace_nada():
    """El sistema debe funcionar sin credenciales configuradas."""
    servicio = _ServicioEspia(_settings(smtp_enabled=False, telegram_enabled=False))
    await servicio.notificar_excursion_critica("ESP32-01", 14.2, "2026-07-25T10:00:00+00:00")

    assert servicio.emails == []
    assert servicio.telegrams == []


@pytest.mark.asyncio
async def test_cooldown_evita_inundar_al_responsable():
    """Un episodio crítico produce una lectura cada pocos segundos; sin
    cooldown el responsable silenciaría el canal, que es el peor resultado."""
    servicio = _ServicioEspia(_settings())
    for _ in range(5):
        await servicio.notificar_excursion_critica("ESP32-01", 14.2, "2026-07-25T10:00:00+00:00")

    assert len(servicio.emails) == 1


@pytest.mark.asyncio
async def test_el_cooldown_es_por_dispositivo():
    """Que un refrigerador esté en cooldown no puede enmascarar la excursión
    de otro."""
    servicio = _ServicioEspia(_settings())
    await servicio.notificar_excursion_critica("ESP32-01", 14.2, "2026-07-25T10:00:00+00:00")
    await servicio.notificar_excursion_critica("ESP32-02", 15.0, "2026-07-25T10:00:01+00:00")

    assert {device for device, _ in servicio.emails} == {"ESP32-01", "ESP32-02"}


@pytest.mark.asyncio
async def test_pasado_el_cooldown_vuelve_a_avisar():
    servicio = _ServicioEspia(_settings())
    await servicio.notificar_excursion_critica("ESP32-01", 14.2, "2026-07-25T10:00:00+00:00")
    # Se retrasa artificialmente el último aviso más allá de la ventana.
    servicio._ultimo_aviso["ESP32-01"] = datetime.now(tz=timezone.utc) - timedelta(minutes=30)
    await servicio.notificar_excursion_critica("ESP32-01", 14.8, "2026-07-25T10:30:00+00:00")

    assert len(servicio.emails) == 2


@pytest.mark.asyncio
async def test_temperatura_nula_se_describe_sin_romper():
    """Sensor caído: no hay valor que reportar, pero el aviso debe salir."""
    servicio = _ServicioEspia(_settings())
    await servicio.notificar_excursion_critica("ESP32-01", None, "2026-07-25T10:00:00+00:00")

    assert servicio.emails == [("ESP32-01", "sin dato de sensor")]


@pytest.mark.asyncio
async def test_fallo_del_canal_no_propaga_excepcion():
    """Un SMTP caído no puede impedir que la lectura se persista y encadene."""

    class _ServicioRoto(NotificacionService):
        def _smtp_enviar(self, mensaje):
            raise OSError("conexión rechazada por el servidor SMTP")

    servicio = _ServicioRoto(_settings())
    # No debe lanzar: el fallo se registra y se traga.
    await servicio.notificar_excursion_critica("ESP32-01", 14.2, "2026-07-25T10:00:00+00:00")


def test_produccion_rechaza_smtp_habilitado_sin_credenciales():
    """Habilitar un canal sin credenciales dejaría al sistema creyendo que
    notifica cuando falla en cada excursión crítica."""
    with pytest.raises(ValueError, match="SMTP_ENABLED requiere"):
        Settings(
            environment="production",
            jwt_secret_key="x" * 40,
            allowed_hosts=["thermotrace.example.org"],
            cors_origins=["https://thermotrace.example.org"],
            database_url="postgresql+asyncpg://u:p@db:5432/prod",
            mqtt_enabled=False,
            smtp_enabled=True,
        )


def test_produccion_rechaza_telegram_habilitado_sin_credenciales():
    with pytest.raises(ValueError, match="TELEGRAM_ENABLED requiere"):
        Settings(
            environment="production",
            jwt_secret_key="x" * 40,
            allowed_hosts=["thermotrace.example.org"],
            cors_origins=["https://thermotrace.example.org"],
            database_url="postgresql+asyncpg://u:p@db:5432/prod",
            mqtt_enabled=False,
            telegram_enabled=True,
        )


def test_produccion_rechaza_sms_habilitado_sin_credenciales():
    with pytest.raises(ValueError, match="SMS_ENABLED requiere"):
        Settings(
            environment="production",
            jwt_secret_key="x" * 40,
            allowed_hosts=["thermotrace.example.org"],
            cors_origins=["https://thermotrace.example.org"],
            database_url="postgresql+asyncpg://u:p@db:5432/prod",
            mqtt_enabled=False,
            sms_enabled=True,
        )


@pytest.mark.asyncio
async def test_sms_se_envia_al_responsable_registrado_del_dispositivo():
    """HU-54: sin destinatario registrado, no hay a quién enviar — este canal
    no tiene equivalente global como el correo (SMTP_TO)."""
    servicio = _ServicioEspia(
        _settings(sms_enabled=True, sms_account_sid="AC", sms_auth_token="tok", sms_from="+10000000000")
    )
    await servicio.notificar_excursion_critica(
        "ESP32-01", 14.2, "2026-07-25T10:00:00+00:00", telefono_destino="+51999999999"
    )
    assert servicio.sms == [("ESP32-01", "14.2 °C")]


@pytest.mark.asyncio
async def test_sms_no_se_envia_sin_telefono_del_responsable():
    servicio = _ServicioEspia(
        _settings(sms_enabled=True, sms_account_sid="AC", sms_auth_token="tok", sms_from="+10000000000")
    )
    await servicio.notificar_excursion_critica("ESP32-01", 14.2, "2026-07-25T10:00:00+00:00")
    assert servicio.sms == []


@pytest.mark.asyncio
async def test_email_usa_el_responsable_del_dispositivo_si_existe():
    """HU-53: el correo se dirige al responsable REGISTRADO del dispositivo,
    no al destinatario global, cuando el dispositivo tiene uno propio."""

    class _ServicioQueRegistraDestinatario(NotificacionService):
        def __init__(self, settings: Settings) -> None:
            super().__init__(settings)
            self.destinatarios: list[str] = []

        def _smtp_enviar(self, mensaje) -> None:
            self.destinatarios.append(mensaje["To"])

    servicio = _ServicioQueRegistraDestinatario(_settings())
    await servicio.notificar_excursion_critica(
        "ESP32-01", 14.2, "2026-07-25T10:00:00+00:00", email_destino="responsable-farm@example.org"
    )
    assert servicio.destinatarios == ["responsable-farm@example.org"]
