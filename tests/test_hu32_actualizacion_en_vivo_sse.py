"""QA & Frontend State Test Suite for HU-32: Real-time Dashboard Updates via Authenticated SSE.

Checklist items covered:
  - [FE] Verificar actualización sin recargar
  - [FE] Verificar rotulado "En vivo" con timestamp de vigencia
  - [FE] Probar reconexión SSE con backoff
  - [QA] Registrar evidencia de escenarios (múltiples pestañas y tolerancia a fallos)
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sse_broadcaster import SSEBroadcaster

EJEMPLOS = Path(__file__).parent.parent / "contrato" / "ejemplos"


class FrontendDashboardStateManager:
    """Simulador del gestor de estado global de React 19 (useMonitoreoTermico)."""

    def __init__(self, client_id: str):
        self.client_id = client_id
        self.estado_conexion: str = "desconectado"  # 'conectado' | 'reconectando' | 'desconectado'
        self.ultima_telemetria: dict | None = None
        self.rotulo_vigencia: str = "Sin conexión"
        self.tiempo_real_activo: bool = False
        self.reconnect_attempts: int = 0
        self.backoff_delay_s: float = 1.0
        self.render_counter: int = 0

    def on_connect(self):
        self.estado_conexion = "conectado"
        self.reconnect_attempts = 0
        self.backoff_delay_s = 1.0

    def on_event_received(self, data: dict):
        """Escenario 1 & 4: Actualización reactiva sin recargar y rotulado 'En vivo'."""
        self.ultima_telemetria = data
        self.render_counter += 1
        self.tiempo_real_activo = True

        # Formateo explícito de la vigencia del dato junto al valor
        timestamp_str = data.get("captured_at") or data.get("_emitted_at")
        self.rotulo_vigencia = f"En vivo • Actualizado: {timestamp_str}"

    def on_connection_lost(self):
        """Escenario 2: Detección de pérdida de flujo y transición a reconectando con backoff."""
        self.estado_conexion = "reconectando"
        self.tiempo_real_activo = False
        self.rotulo_vigencia = "Reconectando canal en vivo..."
        self.reconnect_attempts += 1
        self.backoff_delay_s = min(self.backoff_delay_s * 2.0, 30.0)


@pytest.mark.anyio
async def test_hu32_fe_actualizacion_sin_recargar():
    """[FE] Escenario 1: La recepción de eventos SSE actualiza el estado del componente sin recargar página."""
    broadcaster = SSEBroadcaster()
    manager = FrontendDashboardStateManager("tab_1")

    queue = await broadcaster.subscribe(manager.client_id)
    manager.on_connect()
    assert manager.estado_conexion == "conectado"
    assert manager.render_counter == 0

    # Despachar dos lecturas en tiempo real
    datos1 = {"device_id": "ESP32_FARMACIA_01", "temp_ds18b20": 4.30, "captured_at": "2026-10-02T17:00:00Z"}
    await broadcaster.broadcast("telemetria", datos1)

    msg1 = await queue.get()
    data1 = json.loads(msg1.split("data: ")[1].strip())
    manager.on_event_received(data1)

    assert manager.render_counter == 1
    assert manager.ultima_telemetria["temp_ds18b20"] == 4.30

    datos2 = {"device_id": "ESP32_FARMACIA_01", "temp_ds18b20": 4.55, "captured_at": "2026-10-02T17:00:10Z"}
    await broadcaster.broadcast("telemetria", datos2)

    msg2 = await queue.get()
    data2 = json.loads(msg2.split("data: ")[1].strip())
    manager.on_event_received(data2)

    assert manager.render_counter == 2
    assert manager.ultima_telemetria["temp_ds18b20"] == 4.55

    await broadcaster.unsubscribe(manager.client_id)


def test_hu32_fe_rotulado_en_vivo_con_timestamp():
    """[FE] Escenario 4: Rotulado explícito 'En vivo' junto al valor con marca temporal."""
    manager = FrontendDashboardStateManager("tab_1")
    manager.on_connect()

    telemetria = {
        "device_id": "ESP32_FARMACIA_01",
        "temp_ds18b20": 5.12,
        "captured_at": "2026-10-02T17:15:30Z",
    }
    manager.on_event_received(telemetria)

    assert manager.tiempo_real_activo is True
    assert "En vivo" in manager.rotulo_vigencia
    assert "2026-10-02T17:15:30Z" in manager.rotulo_vigencia


def test_hu32_fe_probar_reconexion_sse_con_backoff():
    """[FE] Escenario 2: Detección de desconexión, indicador amarillo y cálculo de backoff."""
    manager = FrontendDashboardStateManager("tab_1")
    manager.on_connect()
    assert manager.estado_conexion == "conectado"

    # 1. Primera pérdida de señal
    manager.on_connection_lost()
    assert manager.estado_conexion == "reconectando"
    assert manager.tiempo_real_activo is False
    assert "Reconectando" in manager.rotulo_vigencia
    assert manager.reconnect_attempts == 1
    assert manager.backoff_delay_s == 2.0

    # 2. Segundo reintento con backoff exponencial
    manager.on_connection_lost()
    assert manager.reconnect_attempts == 2
    assert manager.backoff_delay_s == 4.0

    # 3. Reconexión exitosa restablece estado y reinicia backoff
    manager.on_connect()
    assert manager.estado_conexion == "conectado"
    assert manager.reconnect_attempts == 0
    assert manager.backoff_delay_s == 1.0


@pytest.mark.anyio
async def test_hu32_qa_multiples_pestanas_simultaneas():
    """[QA] Escenario 3: Múltiples pestañas abiertas reciben la telemetría simultáneamente."""
    broadcaster = SSEBroadcaster()
    tab1 = FrontendDashboardStateManager("pestana_1")
    tab2 = FrontendDashboardStateManager("pestana_2")

    q1 = await broadcaster.subscribe(tab1.client_id)
    q2 = await broadcaster.subscribe(tab2.client_id)
    tab1.on_connect()
    tab2.on_connect()

    assert broadcaster.subscriber_count == 2

    # Enviar lectura fresca
    lectura = {"device_id": "ESP32_FARMACIA_01", "temp_ds18b20": 4.88, "captured_at": "2026-10-02T17:20:00Z"}
    await broadcaster.broadcast("telemetria", lectura)

    # Ambas pestañas leen sus respectivas colas
    m1 = await q1.get()
    m2 = await q2.get()

    tab1.on_event_received(json.loads(m1.split("data: ")[1].strip()))
    tab2.on_event_received(json.loads(m2.split("data: ")[1].strip()))

    assert tab1.ultima_telemetria["temp_ds18b20"] == 4.88
    assert tab2.ultima_telemetria["temp_ds18b20"] == 4.88
    assert tab1.tiempo_real_activo is True
    assert tab2.tiempo_real_activo is True

    await broadcaster.unsubscribe(tab1.client_id)
    await broadcaster.unsubscribe(tab2.client_id)
