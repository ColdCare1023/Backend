"""BE-018: el cooldown antiflood sobrevive entre lecturas."""

from unittest.mock import AsyncMock

import pytest

from src.infrastructure.config import Settings
from src.infrastructure.notifications.notificacion_service import obtener_notificacion_service


def _settings() -> Settings:
    return Settings(telegram_enabled=True, telegram_bot_token="t", telegram_chat_id="c", notificacion_cooldown_minutos=15)


def test_misma_configuracion_misma_instancia():
    settings = _settings()
    assert obtener_notificacion_service(settings) is obtener_notificacion_service(settings)


@pytest.mark.asyncio
async def test_reaperturas_seguidas_notifican_una_sola_vez():
    servicio = obtener_notificacion_service(_settings())
    servicio._enviar_telegram = AsyncMock()

    for _ in range(5):
        # Cada lectura obtiene el servicio de nuevo, como el consumidor MQTT.
        await obtener_notificacion_service(servicio._settings).notificar_excursion_critica("FARM-01", 12.0, "t")

    assert servicio._enviar_telegram.await_count == 1
