"""QA & Backend Validation Test Suite for HU-19: Real-time SSE Event Emission.

Checklist items covered:
  - [BE] Verificar emisión SSE con lectura real
  - [QA] Medir latencia recepción-emisión (< 100 ms)
  - [QA] Registrar evidencia
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "contrato"))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from telemetria_v1 import parsear_telemetria
from sse_broadcaster import SSEBroadcaster

EJEMPLOS = Path(__file__).parent.parent / "contrato" / "ejemplos"


@pytest.mark.anyio
async def test_hu19_be_emision_sse_con_lectura_real():
    """[BE] Escenario 1: Envío de lectura real hacia suscriptores conectados vía SSE."""
    broadcaster = SSEBroadcaster()
    queue = await broadcaster.subscribe("web_client_1")

    raw = (EJEMPLOS / "telemetria_normal.json").read_bytes()
    telemetria = parsear_telemetria(raw)

    t_recepcion = time.time()
    latencia_ms = await broadcaster.broadcast(
        event_type="telemetria",
        data=telemetria.model_dump(mode="json"),
        received_at=t_recepcion,
    )

    # El cliente debe recibir el mensaje inmediatamente
    msg = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert "event: telemetria" in msg
    assert "data: {" in msg
    assert "ESP32_FARMACIA_01" in msg
    assert "4.25" in msg

    data_recibida = json.loads(msg.split("data: ")[1].strip())
    assert data_recibida["temp_ds18b20"] == 4.25
    assert data_recibida["_sse_latency_ms"] >= 0.0

    await broadcaster.unsubscribe("web_client_1")


@pytest.mark.anyio
async def test_hu19_qa_medir_latencia_recepcion_emision():
    """[QA] Medir latencia recepción-emisión: debe ser inferior a 100 ms."""
    broadcaster = SSEBroadcaster()
    queue = await broadcaster.subscribe("perf_client")

    raw = (EJEMPLOS / "telemetria_normal.json").read_bytes()
    telemetria = parsear_telemetria(raw)

    latencias = []
    for _ in range(50):
        t_in = time.time()
        lat_ms = await broadcaster.broadcast(
            event_type="telemetria",
            data=telemetria.model_dump(mode="json"),
            received_at=t_in,
        )
        latencias.append(lat_ms)
        _ = await queue.get()

    latencia_promedio = sum(latencias) / len(latencias)
    latencia_maxima = max(latencias)

    # Criterio estricto de SLA: < 100 ms
    assert latencia_promedio < 100.0, f"Latencia promedio ({latencia_promedio} ms) excede 100 ms"
    assert latencia_maxima < 150.0, f"Latencia pico ({latencia_maxima} ms) excede límite"

    await broadcaster.unsubscribe("perf_client")


@pytest.mark.anyio
async def test_hu19_qa_sin_clientes_conectados_omite_despacho():
    """[QA] Escenario 2: Cuando no hay suscriptores, la emisión se omite para ahorrar recursos."""
    broadcaster = SSEBroadcaster()
    assert broadcaster.subscriber_count == 0

    raw = (EJEMPLOS / "telemetria_normal.json").read_bytes()
    telemetria = parsear_telemetria(raw)

    t_in = time.time()
    lat_ms = await broadcaster.broadcast("telemetria", telemetria.model_dump(mode="json"), received_at=t_in)

    assert broadcaster.total_dispatched == 0
    assert broadcaster.subscriber_count == 0


@pytest.mark.anyio
async def test_hu19_qa_desconexion_abrupta_de_cliente():
    """[QA] Escenario 3: Cliente que satura su cola o se desconecta abruptamente se libera."""
    # Cola pequeña para forzar overflow ante corte de lectura del cliente
    broadcaster = SSEBroadcaster(queue_maxsize=2)
    queue1 = await broadcaster.subscribe("cliente_lento")
    queue2 = await broadcaster.subscribe("cliente_sano")

    raw = (EJEMPLOS / "telemetria_normal.json").read_bytes()
    telemetria = parsear_telemetria(raw)

    # Llenar la cola del cliente lento (el cliente sano consume sus mensajes)
    for _ in range(2):
        await broadcaster.broadcast("telemetria", telemetria.model_dump(mode="json"))
        _ = await queue2.get()  # cliente sano consume a tiempo

    # El siguiente mensaje satura la cola de cliente_lento (queue_maxsize=2)
    # y provoca su desconexión y limpieza, sin afectar al cliente sano
    await broadcaster.broadcast("telemetria", telemetria.model_dump(mode="json"))

    # cliente_lento fue removido automáticamente por desborde
    assert broadcaster.subscriber_count == 1
    # cliente_sano sigue activo y tiene su nuevo mensaje
    assert queue2.qsize() == 1
    _ = await queue2.get()

    await broadcaster.unsubscribe("cliente_sano")

