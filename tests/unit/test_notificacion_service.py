"""HU-23/HU-53/HU-54: armado del aviso y canales opcionales.

Ningún test toca la red: se sustituyen los canales por dobles. El envío del
correo (cuándo, cuántas veces, con qué auditoría) se prueba en
tests/integration/test_hu53_notificacion.py."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.application.use_cases.generar_alerta import AvisoExcursion
from src.infrastructure.config import Settings
from src.infrastructure.notifications.notificacion_service import (
    NotificacionService,
    componer_correo_excursion,
)

CAPTURA = datetime(2026, 7, 25, 15, 0, tzinfo=timezone.utc)


def _settings(**overrides) -> Settings:
    base = {"environment": "test", "telegram_enabled": False}
    return Settings(**{**base, **overrides})


def _aviso(**extra) -> AvisoExcursion:
    base = {
        "alerta_id": uuid4(),
        "device_id": "ESP32-01",
        "temperatura": 14.2,
        "captured_at": CAPTURA,
        "received_at": CAPTURA + timedelta(seconds=3),
        "time_quality": "synced",
    }
    base.update(extra)
    return AvisoExcursion(**base)


class _ServicioEspia(NotificacionService):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.telegrams: list[tuple[str, str]] = []
        self.sms: list[tuple[str, str]] = []

    async def _enviar_telegram(self, device_id, temp_texto, timestamp):
        self.telegrams.append((device_id, temp_texto))

    async def _enviar_sms(self, device_id, temp_texto, timestamp, telefono_destino):
        self.sms.append((device_id, temp_texto))


# ── Armado del correo (HU-53 criterio 6) ─────────────────────────────────


def test_el_correo_permite_actuar_sin_abrir_el_sistema():
    aviso = _aviso()
    correo = componer_correo_excursion(
        aviso,
        remitente="avisos@coldcare.pe",
        destinatario="qf@farmacia.pe",
        frontend_url="https://coldcare.vercel.app/",
        dispositivo={"nombre": "Refrigerador vacunas", "ubicacion": "Almacén 1"},
    )
    assert correo.asunto == "[ColdCare] EXCURSIÓN CRÍTICA — ESP32-01"
    assert "14.2 °C" in correo.cuerpo
    assert "Refrigerador vacunas" in correo.cuerpo
    assert "Almacén 1" in correo.cuerpo
    assert "2–8 °C" in correo.cuerpo
    # 15:00 UTC = 10:00 en Perú (UTC-5, sin horario de verano).
    assert "25/07/2026 10:00:00 (hora de Perú)" in correo.cuerpo
    assert f"https://coldcare.vercel.app/alertas?id={aviso.alerta_id}" in correo.cuerpo


def test_lectura_diferida_se_marca_en_asunto_y_cuerpo():
    aviso = _aviso(time_quality="unsynced")
    correo = componer_correo_excursion(aviso, "de@x.pe", "para@x.pe")
    assert "[DIFERIDA]" in correo.asunto
    assert "DIFERIDA" in correo.cuerpo


def test_temperatura_nula_se_describe_sin_romper():
    """Sensor caído: no hay valor que reportar, pero el aviso debe salir."""
    correo = componer_correo_excursion(_aviso(temperatura=None), "de@x.pe", "para@x.pe")
    assert "sin dato de sensor" in correo.cuerpo


# ── Canales opcionales (HU-54) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_telegram_avisa_cuando_esta_habilitado():
    servicio = _ServicioEspia(_settings(telegram_enabled=True, telegram_bot_token="t", telegram_chat_id="c"))
    await servicio.notificar_excursion_critica("ESP32-01", 14.2, "2026-07-25T10:00:00+00:00")
    assert servicio.telegrams == [("ESP32-01", "14.2 °C")]


@pytest.mark.asyncio
async def test_sin_canales_opcionales_no_hace_nada():
    servicio = _ServicioEspia(_settings())
    await servicio.notificar_excursion_critica("ESP32-01", 14.2, "2026-07-25T10:00:00+00:00")
    assert servicio.telegrams == []
    assert servicio.sms == []


@pytest.mark.asyncio
async def test_cooldown_evita_inundar_por_telegram():
    servicio = _ServicioEspia(_settings(telegram_enabled=True, telegram_bot_token="t", telegram_chat_id="c"))
    for _ in range(5):
        await servicio.notificar_excursion_critica("ESP32-01", 14.2, "2026-07-25T10:00:00+00:00")
    assert len(servicio.telegrams) == 1


@pytest.mark.asyncio
async def test_el_cooldown_es_por_dispositivo():
    servicio = _ServicioEspia(_settings(telegram_enabled=True, telegram_bot_token="t", telegram_chat_id="c"))
    await servicio.notificar_excursion_critica("ESP32-01", 14.2, "2026-07-25T10:00:00+00:00")
    await servicio.notificar_excursion_critica("ESP32-02", 15.0, "2026-07-25T10:00:01+00:00")
    assert {device for device, _ in servicio.telegrams} == {"ESP32-01", "ESP32-02"}


@pytest.mark.asyncio
async def test_pasado_el_cooldown_vuelve_a_avisar():
    servicio = _ServicioEspia(_settings(telegram_enabled=True, telegram_bot_token="t", telegram_chat_id="c"))
    await servicio.notificar_excursion_critica("ESP32-01", 14.2, "2026-07-25T10:00:00+00:00")
    servicio._ultimo_aviso["ESP32-01"] = datetime.now(tz=timezone.utc) - timedelta(minutes=30)
    await servicio.notificar_excursion_critica("ESP32-01", 14.8, "2026-07-25T10:30:00+00:00")
    assert len(servicio.telegrams) == 2


@pytest.mark.asyncio
async def test_sms_se_envia_al_responsable_registrado_del_dispositivo():
    """HU-54: sin destinatario registrado no hay a quién enviar; este canal no
    tiene equivalente global como el correo."""
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


# ── Configuración de producción ──────────────────────────────────────────

_PRODUCCION = {
    "environment": "production",
    "jwt_secret_key": "x" * 40,
    "trazabilidad_hmac_key": "h" * 40,
    "allowed_hosts": ["coldcare.example.org"],
    "cors_origins": ["https://coldcare.example.org"],
    "database_url": "postgresql+asyncpg://u:p@db:5432/prod",
    "mqtt_enabled": False,
}


def test_produccion_rechaza_correo_habilitado_sin_credenciales():
    """Habilitar un canal sin credenciales dejaría al sistema creyendo que
    notifica cuando falla en cada excursión crítica."""
    with pytest.raises(ValueError, match="remitente"):
        Settings(**_PRODUCCION, smtp_enabled=True)
    with pytest.raises(ValueError, match="SMTP_HOST"):
        Settings(
            **_PRODUCCION, smtp_enabled=True, smtp_from="de@x.pe", smtp_to="para@x.pe",
            frontend_url="https://coldcare.example.org",
        )


def test_produccion_rechaza_telegram_habilitado_sin_credenciales():
    with pytest.raises(ValueError, match="TELEGRAM_ENABLED requiere"):
        Settings(**_PRODUCCION, telegram_enabled=True)


def test_produccion_rechaza_sms_habilitado_sin_credenciales():
    with pytest.raises(ValueError, match="SMS_ENABLED requiere"):
        Settings(**_PRODUCCION, sms_enabled=True)
